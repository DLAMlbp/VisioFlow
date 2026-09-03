import asyncio
import logging
from io import BytesIO

from PIL import Image

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.schemas.jobs import ImageItemStatus
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.beautify_planning import neutralize_beautify_profile
from src.services.images.hard_filter import HardFilterService, RejectCode
from src.services.images.metadata import (
    ImageMetadataError,
    ImageMetadataService,
    encode_jpeg,
    make_thumbnail,
)
from src.services.images.processing_vision import (
    PROCESSING_PROMPT_VERSION,
    ProcessingVisionService,
    precise_filter_reason,
)
from src.services.images.quality import QualityEngine
from src.services.images.redaction_policy import evaluate_ground_film
from src.services.jobs.dispatch import CompletionTaskPublisher
from src.services.jobs.progression import advance_after_preprocess as _advance_after_preprocess
from src.services.managed_profiles import (
    beautify_from_snapshot,
    legacy_standard_from_snapshots,
    passthrough_standard,
    redaction_from_snapshot,
    standard_with_global_filter,
    standards_from_snapshots,
)
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="image.preprocess_metadata",
    queue="preprocess",
    max_retries=0,
)
def preprocess_image_metadata(image_id: str) -> None:
    asyncio.run(_preprocess_image_metadata(image_id))


async def _preprocess_image_metadata(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is None:
            return
        if item.status == ImageItemStatus.FILTERED.value:
            await _advance_after_preprocess(repository, item)
            return
        if item.status in {
            ImageItemStatus.ENHANCING.value,
            ImageItemStatus.ENHANCED.value,
            ImageItemStatus.TAGGING.value,
            ImageItemStatus.REJECTED.value,
            ImageItemStatus.SELECTED.value,
            ImageItemStatus.FAILED.value,
        }:
            return

        item = await repository.claim_preprocess(image_id)
        if item is None:
            return
        settings = load_ai_model_settings(get_settings())
        job = await repository.get_config(item.job_id)
        if job is None:
            return
        standards = standards_from_snapshots(job.processing_standard_snapshots)
        if not job.filter_enabled:
            standards = [passthrough_standard()]
        elif not standards:
            standards = [
                legacy_standard_from_snapshots(
                    job.filter_profile_snapshot,
                    job.beautify_profile_snapshot,
                    job.filter_profile_id,
                    job.beautify_profile_id,
                )
            ]
        elif job.filter_enabled:
            standards = [
                standard_with_global_filter(standard, job.filter_profile_snapshot)
                for standard in standards
            ]
        beautify_profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        redaction_profile = redaction_from_snapshot(
            getattr(job, "redaction_profile_snapshot", None),
            legacy_beautify_snapshot=getattr(job, "beautify_profile_snapshot", None),
        )
        storage = get_storage_provider()

        service = ImageMetadataService(storage, settings)
        try:
            metadata = await service.process(
                job_id=item.job_id,
                image_id=item.id,
                object_key=item.object_key,
            )
        except ImageMetadataError as exc:
            await repository.fail_item(item, str(exc))
            await _advance_after_preprocess(repository, item)
            return

        beautify_service = NaturalBeautifyService()
        neutral_beautify_profile = neutralize_beautify_profile(beautify_profile)
        orientation_result = beautify_service.normalize_orientation(
            metadata.original_bytes,
            neutral_beautify_profile,
        )
        with Image.open(BytesIO(orientation_result.image_bytes)) as normalized_image:
            normalized_image.load()
            normalized_width, normalized_height = normalized_image.size
            normalized_thumbnail = encode_jpeg(
                make_thumbnail(normalized_image, settings.thumbnail_long_side)
            )

        duplicate, similar = await repository.save_metadata_and_find_duplicates(
            item,
            {
                "content_type": metadata.content_type,
                "file_size": metadata.file_size,
                "width": normalized_width,
                "height": normalized_height,
                "aspect_ratio": round(normalized_width / normalized_height, 4),
                "exif_orientation": metadata.orientation,
                "sha256": metadata.sha256,
                "phash": metadata.phash,
                "thumbnail_object_key": metadata.thumbnail_object_key,
            },
            max_hamming_distance=settings.technical_duplicate_hamming_distance,
        )
        if duplicate is not None:
            await repository.reject_item(item, [RejectCode.DUPLICATE_IMAGE])
            await _advance_after_preprocess(repository, item)
            return

        warnings: list[str] = []
        if similar is not None:
            warnings.append("与同批次照片构图相似，已保留，请按需确认是否重复")

        hard_filter = HardFilterService(settings)
        filter_result = hard_filter.evaluate(
            normalized_thumbnail,
            width=normalized_width,
            height=normalized_height,
        )
        if not filter_result.passed:
            await repository.reject_item(item, list(filter_result.reject_codes))
            await _advance_after_preprocess(repository, item)
            return

        quality_metrics = QualityEngine(settings).evaluate(normalized_thumbnail)
        await repository.upsert_metrics(item.id, quality_metrics.as_db_values())
        final_score = QualityEngine.calculate_weighted_quality_score(
            sharpness=quality_metrics.sharpness_score,
            exposure=quality_metrics.exposure_score,
            contrast=quality_metrics.contrast_score,
            noise=quality_metrics.noise_score,
        )
        if job.routing_mode in {"completion", "standards", "streaming_v2"}:
            if await repository.complete_metadata_for_completion(item):
                CompletionTaskPublisher().publish(item.id)
            return
        ai_outcome = await ProcessingVisionService(settings).analyze(
            orientation_result.image_bytes,
            standards=standards,
            unmatched_standard_policy=job.unmatched_standard_policy,
            image_context={
                "width": normalized_width,
                "height": normalized_height,
                "sharpness_score": quality_metrics.sharpness_score,
                "exposure_score": quality_metrics.exposure_score,
                "contrast_score": quality_metrics.contrast_score,
                "noise_score": quality_metrics.noise_score,
                "quality_score": final_score,
            },
            redaction_profile=redaction_profile,
        )
        await repository.save_ai_processing(
            item,
            status=ai_outcome.status,
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=ai_outcome.duration_ms,
            payload=(
                ai_outcome.payload.model_dump(mode="json")
                if ai_outcome.payload is not None
                else None
            ),
            error_message=ai_outcome.error_message,
        )
        if ai_outcome.status != "completed" or ai_outcome.payload is None:
            await repository.fail_item(
                item,
                f"AI 图片处理失败：{ai_outcome.error_message or '模型未返回有效结果'}",
            )
            await _advance_after_preprocess(repository, item)
            return
        selection = ai_outcome.payload.standard_selection
        if selection is None:
            await repository.fail_item(
                item,
                "AI 未返回条件处理标准评估结果",
            )
            await _advance_after_preprocess(repository, item)
            return
        selected_standard = next(
            (standard for standard in standards if standard.id == selection.selected_standard_id),
            None,
        )
        ground_film = evaluate_ground_film(
            redaction_profile, ai_outcome.payload.redaction_analysis
        )
        if ground_film.rejected:
            await repository.reject_item(
                item,
                [RejectCode.BRANDED_GROUND_FILM_COVERAGE],
                reason=ground_film.reason,
            )
            await _advance_after_preprocess(repository, item)
            return
        if ground_film.review_required:
            await repository.mark_review_required(item.id)
            warnings.append(ground_film.reason)
        if (
            job.filter_enabled
            and ai_outcome.status == "completed"
            and ai_outcome.payload.filter.rejected
        ):
            standard_name = (
                selected_standard.name or selected_standard.description
                if selected_standard is not None
                else None
            )
            await repository.reject_item(
                item,
                [RejectCode.AI_FILTER_REJECTED],
                reason=precise_filter_reason(
                    ai_outcome.payload.filter,
                    standard_name=standard_name,
                ),
            )
            await _advance_after_preprocess(repository, item)
            return
        if not job.filter_enabled:
            warnings.append("已跳过条件筛选")
        elif selected_standard is not None:
            warnings.append(
                f"已启用处理标准：{selected_standard.name or selected_standard.description}；"
                f"{selection.reason}"
            )
        else:
            warnings.append(f"未命中条件处理标准；{selection.reason}")
        if job.filter_enabled:
            warnings.append(f"AI 筛选：{ai_outcome.payload.filter.reason}")

        await repository.complete_filter(
            item,
            final_score=final_score,
            reasons=warnings,
        )
        await _advance_after_preprocess(repository, item)


def _profile_instruction(snapshot: dict[str, object] | None, fallback: str) -> str:
    if snapshot and isinstance(snapshot.get("instruction"), str):
        instruction = str(snapshot["instruction"]).strip()
        if instruction:
            return instruction
    return fallback
