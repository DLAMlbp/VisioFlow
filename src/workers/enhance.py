from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from celery import Task
from PIL import Image

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.beautify_acceptance import (
    acceptance_passed,
    correct_after_preview,
    evaluate_acceptance,
    make_preview,
)
from src.services.images.beautify_planning import (
    beautify_plan_from_json,
    neutralize_beautify_profile,
)
from src.services.images.beautify_policy import validate_beautify_plan
from src.services.images.metadata import encode_jpeg, make_thumbnail
from src.services.images.processing_vision import selected_standard_from_processing_json
from src.services.images.quality import QualityEngine
from src.services.jobs.dispatch import AnalysisTaskPublisher, EmbeddingTaskPublisher
from src.services.managed_profiles import (
    beautify_from_snapshot,
)
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import build_analysis_object_key, build_enhanced_object_key
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class ImageEnhancementTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_mark_enhancement_failed(image_id))
            except Exception:
                logger.exception("Unable to mark the failed enhancement")


@celery_app.task(
    bind=True,
    base=ImageEnhancementTask,
    name="image.enhance",
    queue="enhance",
    max_retries=3,
    default_retry_delay=10,
)
def enhance_image(task, image_id: str) -> None:
    try:
        asyncio.run(_enhance_image(image_id))
    except Exception as exc:
        raise task.retry(exc=exc, countdown=10) from exc


async def _enhance_image(image_id: str) -> None:
    if not get_settings().post_filter_beautify_plan_enabled:
        logger.error(
            "Post-filter beautify planning is disabled; enhancement remains pending image_id=%s",
            image_id,
        )
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_enhancement(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        selected_standard_id, _ = selected_standard_from_processing_json(item.ai_processing_json)
        if item.routed_filter_profile_id and selected_standard_id != item.routed_filter_profile_id:
            await repository.fail_item(item, "命中的条件过滤标准不存在，请重试图片处理")
            return
        profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        storage = get_storage_provider()
        beautify_service = NaturalBeautifyService()
        original_bytes = await storage.download(item.object_key)
        neutral_profile = neutralize_beautify_profile(profile)
        orientation_result = beautify_service.normalize_orientation(original_bytes, neutral_profile)
        stored_plan = beautify_plan_from_json(item.beautify_plan_json)
        if job.beautify_enabled and stored_plan is None:
            await repository.fail_item(item, "缺少 AI 美化决策，请重试图片处理")
            return
        ai_beautify = stored_plan.decision if stored_plan is not None else None
        policy_corrections: list[str] = (
            list(stored_plan.corrections) if stored_plan is not None else []
        )
        if job.beautify_enabled and ai_beautify is not None:
            try:
                policy_result = validate_beautify_plan(
                    profile,
                    needed=ai_beautify.needed,
                    parameters=ai_beautify.parameters.model_dump(),
                )
            except ValueError as exc:
                await repository.fail_item(item, str(exc))
                return
            effective_profile = policy_result.profile
            policy_corrections.extend(policy_result.corrections)
        else:
            effective_profile = neutral_profile

        preview_attempts: list[dict[str, object]] = []
        fallback_reason: str | None = None
        execution_needed = bool(
            job.beautify_enabled and ai_beautify is not None and ai_beautify.needed
        )
        if execution_needed:
            preview_bytes = make_preview(original_bytes)
            preview_profile = effective_profile.model_copy(update={"min_output_long_side": 1})
            trial_bytes = beautify_service.enhance(preview_bytes, preview_profile)
            preview_checks = evaluate_acceptance(preview_bytes, trial_bytes)
            preview_attempts.append({"attempt": 1, "checks": preview_checks})
            if not acceptance_passed(preview_checks):
                correction = correct_after_preview(effective_profile, preview_checks)
                effective_profile = correction.profile
                policy_corrections.extend(correction.reasons)
                corrected_preview_profile = effective_profile.model_copy(
                    update={"min_output_long_side": 1}
                )
                corrected_trial_bytes = beautify_service.enhance(
                    preview_bytes, corrected_preview_profile
                )
                corrected_checks = evaluate_acceptance(preview_bytes, corrected_trial_bytes)
                preview_attempts.append({"attempt": 2, "checks": corrected_checks})
                if not acceptance_passed(corrected_checks):
                    fallback_reason = "小图预演经一次参数修正后仍未通过安全验收"
                    effective_profile = neutral_profile
                    execution_needed = False

        if execution_needed:
            beautify_result = beautify_service.enhance_with_details(
                original_bytes, effective_profile
            )
            enhanced_bytes = beautify_result.image_bytes
            reasons = beautify_service.processing_reasons(effective_profile, beautify_result)
            reasons.insert(0, f"AI 美化：{ai_beautify.reason}")
            reasons.extend(
                f"参数策略修正：{reason}" for reason in dict.fromkeys(policy_corrections)
            )
        else:
            enhanced_bytes = beautify_service.prepare_delivery_image(
                orientation_result.image_bytes, neutral_profile
            )
            reasons = (
                [f"AI 美化：{ai_beautify.reason}，无需额外调整"]
                if ai_beautify is not None and job.beautify_enabled and not fallback_reason
                else ["已跳过美化，仅校正方向并规范输出格式"]
            )
            if fallback_reason:
                reasons = [f"美化安全回退：{fallback_reason}"]

        final_checks = evaluate_acceptance(
            orientation_result.image_bytes,
            enhanced_bytes,
        )
        if execution_needed and not acceptance_passed(final_checks):
            fallback_reason = "正式图最终验收未通过，已回退为中性输出"
            effective_profile = neutral_profile
            enhanced_bytes = beautify_service.prepare_delivery_image(
                orientation_result.image_bytes, neutral_profile
            )
            final_checks = evaluate_acceptance(
                orientation_result.image_bytes,
                enhanced_bytes,
            )
            reasons = [f"美化安全回退：{fallback_reason}"]

        planned_parameters = (
            ai_beautify.parameters.model_dump(mode="json") if ai_beautify is not None else {}
        )
        effective_parameters = {
            name: getattr(effective_profile, name) for name in planned_parameters
        }
        enhancement_audit = {
            "profile_snapshot": job.beautify_profile_snapshot,
            "planned_parameters": planned_parameters,
            "effective_parameters": effective_parameters,
            "corrections": list(dict.fromkeys(policy_corrections)),
            "preview_attempts": preview_attempts,
            "acceptance": {
                "status": (
                    "fallback"
                    if fallback_reason
                    else "passed"
                    if acceptance_passed(final_checks)
                    else "failed"
                ),
                "checks": final_checks,
                "fallback_reason": fallback_reason,
            },
        }

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
        enhancement_saved = await repository.complete_enhancement(
            item,
            enhanced_object_key=enhanced_object_key,
            analysis_object_key=analysis_object_key,
            enhanced_metrics=enhanced_metric_values,
            reasons=reasons,
            enhancement_audit=enhancement_audit,
        )
        if not enhancement_saved:
            return
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
            EmbeddingTaskPublisher().publish(item.id)


async def _mark_enhancement_failed(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        emit_metric(
            logger,
            "enhancement_failures_total",
            labels={
                "image_id": image_id,
                "job_id": item.job_id if item is not None else None,
            },
        )
        if item is not None and item.status in {"filtered", "enhancing", "enhanced"}:
            await repository.fail_item(item, "图片美化任务多次重试后仍失败")
