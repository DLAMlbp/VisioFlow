from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from time import perf_counter

from PIL import Image

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.enhancement_pipeline import (
    decode_pipeline_state,
    pipeline_state_object_key,
)
from src.services.images.logo_detector import load_logo_detector
from src.services.images.logo_overlay import apply_logo_overlays
from src.services.images.metadata import encode_jpeg, make_thumbnail
from src.services.images.quality import QualityEngine
from src.services.images.redaction import (
    ImageRedactionService,
    changed_pixels_outside_boxes,
    decode_image,
)
from src.services.images.redaction import encode_jpeg as encode_redaction_jpeg
from src.services.jobs.dispatch import AnalysisTaskPublisher, EmbeddingTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot, redaction_from_snapshot
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import (
    build_analysis_object_key,
    build_enhanced_object_key,
    build_redaction_base_object_key,
)
from src.workers.celery_app import celery_app
from src.workers.enhancement_common import EnhancementStageTask

logger = logging.getLogger(__name__)


class RenderStageTask(EnhancementStageTask):
    stage_name = "logo_and_render"
    cursor_stage = "render"


@celery_app.task(
    bind=True,
    base=RenderStageTask,
    name="image.render_image",
    queue="render",
    max_retries=3,
    default_retry_delay=10,
)
def render_image(task, image_id: str) -> None:
    try:
        asyncio.run(_render_image(image_id))
    except Exception as exc:
        raise task.retry(exc=exc, countdown=10) from exc


async def _render_image(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.continue_enhancement(image_id, "render")
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return

        settings = load_ai_model_settings(get_settings())
        beautify_profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        redaction_profile = redaction_from_snapshot(
            job.redaction_profile_snapshot,
            legacy_beautify_snapshot=job.beautify_profile_snapshot,
        )
        storage = get_storage_provider()
        state = decode_pipeline_state(
            await storage.download(pipeline_state_object_key(item.job_id, item.id))
        )
        if state.get("stage") != "render":
            return
        beautified_bytes = await storage.download(str(state["beautified_object_key"]))

        watermark_audit = dict(state.get("watermark_audit") or {})
        watermark_detected = watermark_audit.get("status") == "detected"
        if redaction_profile.logo.enabled or watermark_detected:
            await storage.upload(
                build_redaction_base_object_key(item.job_id, item.id),
                beautified_bytes,
                "image/jpeg",
            )
        render_input = decode_image(beautified_bytes)
        if job.watermark_processing_enabled and watermark_detected:
            started = perf_counter()
            watermark_boxes = _scaled_watermark_boxes(
                watermark_audit,
                width=render_input.shape[1],
                height=render_input.shape[0],
            )
            try:
                watermark_image, rendered_boxes, asset_sha256 = apply_logo_overlays(
                    render_input,
                    watermark_boxes,
                    asset_id=redaction_profile.logo.overlay_asset_id,
                    expansion=0.0,
                    scale=redaction_profile.logo.overlay_scale,
                )
                watermark_audit.update(
                    {
                        "status": "applied" if rendered_boxes else "not_detected",
                        "detections": len(watermark_boxes),
                        "rendered_boxes": [list(box) for box in rendered_boxes],
                        "overlay_asset_id": redaction_profile.logo.overlay_asset_id,
                        "overlay_asset_sha256": asset_sha256,
                        "outside_boxes_changed_pixels": changed_pixels_outside_boxes(
                            render_input, watermark_image, rendered_boxes
                        ),
                        "render_duration_ms": round((perf_counter() - started) * 1000),
                    }
                )
                watermark_audit["duration_ms"] = int(
                    watermark_audit.get("detection_duration_ms") or 0
                ) + int(watermark_audit.get("render_duration_ms") or 0)
                render_input = watermark_image
            except Exception as exc:  # noqa: BLE001
                logger.warning("Watermark APP overlay failed safely", exc_info=True)
                watermark_audit.update(
                    {
                        "status": "failed_safe",
                        "render_duration_ms": round((perf_counter() - started) * 1000),
                        "error": str(exc)[:300],
                    }
                )

        logo_result = ImageRedactionService(
            logo_detector=load_logo_detector(settings)
        ).mosaic_logos(
            render_input,
            redaction_profile.logo,
        )
        logo_result.audit["manual_review_available"] = redaction_profile.logo.enabled
        enhanced_bytes = (
            encode_redaction_jpeg(
                logo_result.image_bgr,
                quality=beautify_profile.jpeg_quality,
            )
            if (
                logo_result.audit.get("status") == "applied"
                or watermark_audit.get("status") == "applied"
            )
            else beautified_bytes
        )

        for stage, audit in (
            ("watermark", watermark_audit),
            ("logo", logo_result.audit),
        ):
            metric_labels = {
                "stage": stage,
                "status": audit.get("status"),
                "version": audit.get("profile_version") or audit.get("model_version"),
            }
            emit_metric(logger, "redaction_stage_total", labels=metric_labels)
            emit_metric(
                logger,
                "redaction_stage_duration_ms",
                value=float(
                    audit.get("duration_ms")
                    or audit.get("inpaint_duration_ms")
                    or audit.get("detection_duration_ms")
                    or 0
                ),
                labels=metric_labels,
            )
        emit_metric(
            logger,
            "redaction_logo_detections_total",
            value=float(logo_result.audit.get("detections") or 0),
            labels={"model_version": logo_result.audit.get("model_version")},
        )

        beautify_audit = dict(state.get("beautify_audit") or {})
        enhancement_audit = {
            **beautify_audit,
            "redaction_profile_snapshot": job.redaction_profile_snapshot,
            "redaction": {
                "standard": {
                    "id": redaction_profile.id,
                    "version": redaction_profile.version,
                    "description": redaction_profile.description,
                    "ground_film_threshold": (
                        redaction_profile.branded_ground_film.reject_coverage_gte
                    ),
                },
                "screening": (
                    item.ai_processing_json.get("redaction_analysis")
                    if isinstance(item.ai_processing_json, dict)
                    else None
                ),
                "watermark": watermark_audit,
                "logos": logo_result.audit,
            },
        }
        watermark_reasons = list(state.get("watermark_reasons") or [])
        if watermark_audit.get("status") == "applied":
            watermark_reasons = [
                "已使用透明小当图标遮挡左下角拍摄水印中的英文 APP"
            ]
        elif watermark_audit.get("status") == "failed_safe":
            watermark_reasons = ["水印 APP 遮挡失败，已安全保留当前画面"]
        reasons = [
            *list(state.get("beautify_reasons") or []),
            *watermark_reasons,
            *logo_result.reasons,
        ]

        enhanced_object_key = build_enhanced_object_key(item.job_id, item.id)
        analysis_object_key = (
            build_analysis_object_key(item.job_id, item.id)
            if job.similarity_enabled
            else enhanced_object_key
        )
        await storage.upload(enhanced_object_key, enhanced_bytes, "image/jpeg")
        with Image.open(BytesIO(enhanced_bytes)) as enhanced_image:
            enhanced_image.load()
            enhanced_thumbnail = encode_jpeg(
                make_thumbnail(enhanced_image, settings.thumbnail_long_side)
            )
            if job.similarity_enabled:
                analysis_bytes = encode_jpeg(
                    make_thumbnail(enhanced_image, settings.ai_tagging_image_long_side)
                )
        if job.similarity_enabled:
            await storage.upload(analysis_object_key, analysis_bytes, "image/jpeg")
        enhanced_metrics = QualityEngine(settings).evaluate(enhanced_thumbnail)
        enhanced_metric_values = {
            "sharpness": enhanced_metrics.sharpness_score,
            "exposure": enhanced_metrics.exposure_score,
            "contrast": enhanced_metrics.contrast_score,
            "noise": enhanced_metrics.noise_score,
        }
        saved = await repository.complete_enhancement(
            item,
            enhanced_object_key=enhanced_object_key,
            analysis_object_key=analysis_object_key,
            enhanced_metrics=enhanced_metric_values,
            reasons=reasons,
            enhancement_audit=enhancement_audit,
        )
        if not saved:
            return

        emit_metric(
            logger,
            "enhancement_stage_total",
            labels={
                "stage": "logo_and_render",
                "outcome": "completed",
                "image_id": item.id,
                "job_id": item.job_id,
            },
        )
        if not job.similarity_enabled:
            await repository.select_item(
                item,
                enhanced_object_key,
                float(item.result.final_score or 0) if item.result else 0,
                reasons=[*reasons, "已跳过素材相似匹配"],
                enhanced_metrics=enhanced_metric_values,
            )
            return
        if get_settings().early_semantic_branch_enabled:
            if await repository.queue_final_embedding(item.id):
                EmbeddingTaskPublisher().publish(item.id)
                return
            refreshed = await repository.get_item(item.id)
            if refreshed is not None:
                match_reason = (
                    refreshed.similarity_match.message
                    if refreshed.similarity_match is not None
                    else "素材库匹配完成"
                )
                await repository.finalize_selected_if_ready(
                    refreshed,
                    reason=match_reason,
                )
            return
        if await repository.start_tagging(
            item,
            final_score=float(item.result.final_score or 0) if item.result else 0,
            reasons=reasons,
            enhanced_object_key=enhanced_object_key,
            enhanced_metrics=enhanced_metric_values,
            provider="library",
            model_name=settings.image_embedding_version,
            prompt_version=job.similarity_profile_id,
        ):
            AnalysisTaskPublisher().publish(item.id)


def _scaled_watermark_boxes(
    audit: dict[str, object], *, width: int, height: int
) -> list[tuple[int, int, int, int]]:
    image_size = audit.get("image_size")
    raw_boxes = audit.get("automatic_boxes")
    if not isinstance(image_size, list) or len(image_size) != 2:
        return []
    if not isinstance(raw_boxes, list):
        return []
    source_width, source_height = image_size
    if not isinstance(source_width, (int, float)) or not isinstance(
        source_height, (int, float)
    ):
        return []
    scale_x = width / max(1.0, float(source_width))
    scale_y = height / max(1.0, float(source_height))
    boxes: list[tuple[int, int, int, int]] = []
    for raw_box in raw_boxes:
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            continue
        if not all(isinstance(value, (int, float)) for value in raw_box):
            continue
        x0, y0, x1, y1 = raw_box
        box = (
            max(0, min(width, round(float(x0) * scale_x))),
            max(0, min(height, round(float(y0) * scale_y))),
            max(0, min(width, round(float(x1) * scale_x))),
            max(0, min(height, round(float(y1) * scale_y))),
        )
        if box[2] > box[0] and box[3] > box[1]:
            boxes.append(box)
    return boxes
