from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.enhancement_pipeline import (
    decode_mask,
    decode_pipeline_state,
    encode_pipeline_state,
    pipeline_state_object_key,
)
from src.services.images.redaction import decode_image, encode_jpeg
from src.services.images.watermark import WatermarkPreparation, load_watermark_processor
from src.services.jobs.dispatch import EnhancementTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot, redaction_from_snapshot
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app
from src.workers.enhancement_common import EnhancementStageTask

logger = logging.getLogger(__name__)


class InpaintStageTask(EnhancementStageTask):
    stage_name = "watermark_inpaint"
    cursor_stage = "inpaint"


@celery_app.task(
    base=InpaintStageTask,
    name="image.inpaint_watermark",
    queue="inpaint",
    max_retries=0,
    soft_time_limit=180,
    time_limit=185,
)
def inpaint_watermark(image_id: str) -> None:
    asyncio.run(_inpaint_watermark(image_id))


async def _inpaint_watermark(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.continue_enhancement(image_id, "inpaint")
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
        state_key = pipeline_state_object_key(item.job_id, item.id)
        state = decode_pipeline_state(await storage.download(state_key))
        if state.get("stage") != "inpaint":
            if state.get("stage") == "enhance":
                if await repository.advance_enhancement_stage(
                    item.id,
                    current_stage="inpaint",
                    next_stage="enhance",
                ):
                    EnhancementTaskPublisher().publish(item.id)
                return
            raise ValueError(
                f"unexpected enhancement state during inpaint: {state.get('stage')}"
            )
        if not state.get("watermark_requires_inpaint"):
            if await repository.advance_enhancement_stage(
                item.id,
                current_stage="inpaint",
                next_stage="enhance",
            ):
                EnhancementTaskPublisher().publish(item.id)
            return

        normalized_key = str(state["normalized_object_key"])
        mask_key = str(state["watermark_mask_object_key"])
        watermark_key = str(state["watermark_object_key"])
        image_bgr = decode_image(await storage.download(normalized_key))
        mask = decode_mask(await storage.download(mask_key))
        preparation = WatermarkPreparation(
            image_bgr=image_bgr,
            mask=mask,
            audit=dict(state.get("watermark_audit") or {}),
            requires_inpaint=True,
        )

        processor = load_watermark_processor(settings)
        started = perf_counter()
        try:
            result = processor.apply_prepared(preparation, redaction_profile.watermark)
            audit = dict(result.audit)
            audit["profile_version"] = processor.profile_version
            audit["inpaint_duration_ms"] = round((perf_counter() - started) * 1000)
            reasons = list(result.reasons)
            output_bgr = result.image_bgr
        except Exception as exc:
            logger.warning("Watermark inpaint failed safely", exc_info=True)
            audit = dict(preparation.audit)
            audit.update(
                {
                    "status": "failed_safe",
                    "profile_version": processor.profile_version,
                    "inpaint_duration_ms": round((perf_counter() - started) * 1000),
                    "error": str(exc)[:300],
                }
            )
            reasons = ["左下角水印修复失败，已安全保留原画面"]
            output_bgr = image_bgr

        await storage.upload(
            watermark_key,
            encode_jpeg(output_bgr, quality=beautify_profile.jpeg_quality),
            "image/jpeg",
        )
        state["watermark_requires_inpaint"] = False
        state["watermark_audit"] = audit
        state["watermark_reasons"] = reasons
        state["stage"] = "enhance"
        await storage.upload(
            state_key,
            encode_pipeline_state(state),
            "application/json",
        )
        if not await repository.advance_enhancement_stage(
            item.id,
            current_stage="inpaint",
            next_stage="enhance",
        ):
            return
        emit_metric(
            logger,
            "enhancement_stage_total",
            labels={
                "stage": "watermark_inpaint",
                "outcome": audit.get("status"),
                "image_id": item.id,
                "job_id": item.job_id,
            },
        )
        EnhancementTaskPublisher().publish(item.id)
