from __future__ import annotations

import asyncio
import logging

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
from src.services.images.enhancement_pipeline import (
    decode_pipeline_state,
    encode_pipeline_state,
    pipeline_object_key,
    pipeline_state_object_key,
)
from src.services.jobs.dispatch import RenderTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="image.enhance",
    queue="enhance",
    max_retries=0,
)
def enhance_image(image_id: str) -> None:
    asyncio.run(_enhance_image(image_id))


async def _enhance_image(image_id: str) -> None:
    if not get_settings().post_filter_beautify_plan_enabled:
        logger.error(
            "Post-filter beautify planning is disabled; enhancement remains pending image_id=%s",
            image_id,
        )
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.continue_enhancement(image_id, "enhance")
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        storage = get_storage_provider()
        state_key = pipeline_state_object_key(item.job_id, item.id)
        state = decode_pipeline_state(await storage.download(state_key))
        if state.get("stage") != "enhance":
            if state.get("stage") == "render":
                if await repository.advance_enhancement_stage(
                    item.id,
                    current_stage="enhance",
                    next_stage="render",
                ):
                    RenderTaskPublisher().publish(item.id)
                return
            raise ValueError(
                f"unexpected enhancement state during beautify: {state.get('stage')}"
            )
        beautify_input_bytes = await storage.download(str(state["watermark_object_key"]))
        neutral_profile = neutralize_beautify_profile(profile)
        stored_plan = beautify_plan_from_json(item.beautify_plan_json)
        if job.beautify_enabled and stored_plan is None:
            await repository.fail_item(item, "缺少 AI 美化决策，请重试图片处理")
            return
        ai_beautify = stored_plan.decision if stored_plan is not None else None
        policy_corrections = list(stored_plan.corrections) if stored_plan is not None else []
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

        beautify_service = NaturalBeautifyService()
        preview_attempts: list[dict[str, object]] = []
        fallback_reason: str | None = None
        execution_needed = bool(
            job.beautify_enabled and ai_beautify is not None and ai_beautify.needed
        )
        if execution_needed:
            preview_bytes = make_preview(beautify_input_bytes)
            preview_profile = effective_profile.model_copy(update={"min_output_long_side": 1})
            trial_bytes = beautify_service.enhance(preview_bytes, preview_profile)
            preview_checks = evaluate_acceptance(preview_bytes, trial_bytes)
            preview_attempts.append({"attempt": 1, "checks": preview_checks})
            if not acceptance_passed(preview_checks):
                correction = correct_after_preview(effective_profile, preview_checks)
                effective_profile = correction.profile
                policy_corrections.extend(correction.reasons)
                corrected_profile = effective_profile.model_copy(
                    update={"min_output_long_side": 1}
                )
                corrected_trial = beautify_service.enhance(preview_bytes, corrected_profile)
                corrected_checks = evaluate_acceptance(preview_bytes, corrected_trial)
                preview_attempts.append({"attempt": 2, "checks": corrected_checks})
                if not acceptance_passed(corrected_checks):
                    fallback_reason = "小图预演经一次参数修正后仍未通过安全验收"
                    effective_profile = neutral_profile
                    execution_needed = False

        if execution_needed:
            beautify_result = beautify_service.enhance_with_details(
                beautify_input_bytes, effective_profile
            )
            enhanced_bytes = beautify_result.image_bytes
            reasons = beautify_service.processing_reasons(effective_profile, beautify_result)
            reasons.insert(0, f"AI 美化：{ai_beautify.reason}")
            reasons.extend(
                f"参数策略修正：{reason}" for reason in dict.fromkeys(policy_corrections)
            )
        else:
            enhanced_bytes = beautify_service.prepare_delivery_image(
                beautify_input_bytes, neutral_profile
            )
            reasons = (
                [f"AI 美化：{ai_beautify.reason}，无需额外调整"]
                if ai_beautify is not None and job.beautify_enabled and not fallback_reason
                else ["已跳过美化，仅校正方向并规范输出格式"]
            )
            if fallback_reason:
                reasons = [f"美化安全回退：{fallback_reason}"]

        final_checks = evaluate_acceptance(beautify_input_bytes, enhanced_bytes)
        if execution_needed and not acceptance_passed(final_checks):
            fallback_reason = "正式图最终验收未通过，已回退为中性输出"
            effective_profile = neutral_profile
            enhanced_bytes = beautify_service.prepare_delivery_image(
                beautify_input_bytes, neutral_profile
            )
            final_checks = evaluate_acceptance(beautify_input_bytes, enhanced_bytes)
            reasons = [f"美化安全回退：{fallback_reason}"]

        planned_parameters = (
            ai_beautify.parameters.model_dump(mode="json") if ai_beautify is not None else {}
        )
        state["beautify_audit"] = {
            "profile_snapshot": job.beautify_profile_snapshot,
            "planned_parameters": planned_parameters,
            "effective_parameters": {
                name: getattr(effective_profile, name) for name in planned_parameters
            },
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
        state["beautify_reasons"] = reasons
        state["stage"] = "render"
        beautified_key = pipeline_object_key(item.job_id, item.id, "beautified", "jpg")
        state["beautified_object_key"] = beautified_key
        await storage.upload(beautified_key, enhanced_bytes, "image/jpeg")
        await storage.upload(
            state_key,
            encode_pipeline_state(state),
            "application/json",
        )
        if not await repository.advance_enhancement_stage(
            item.id,
            current_stage="enhance",
            next_stage="render",
        ):
            return
        emit_metric(
            logger,
            "enhancement_stage_total",
            labels={
                "stage": "beautify",
                "outcome": state["beautify_audit"]["acceptance"]["status"],
                "image_id": item.id,
                "job_id": item.job_id,
            },
        )
        RenderTaskPublisher().publish(item.id)
