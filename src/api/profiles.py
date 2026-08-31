from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.db.session import get_db_session
from src.models.processing_profile import ProcessingProfile
from src.services.managed_profiles import ManagedProfileError, ManagedProfileService, ProfileType
from src.services.profiles import ProcessingStandard, ProfileLoader

router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class ProfileOptionResponse(BaseModel):
    id: str
    name: str
    description: str
    version: int = 1
    status: str = "active"
    editable: bool = True


class ProfileDetailResponse(ProfileOptionResponse):
    profile_type: Literal["filter", "beautify", "completion"]
    instruction: str
    config: dict[str, object]


class ProfilePreviewRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class ProfilePreviewResponse(BaseModel):
    description: str
    config: dict[str, object]
    unsupported: list[str]
    can_save: bool


class SaveProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=3, max_length=2000)
    description: str = Field(min_length=1, max_length=500)
    config: dict[str, object]
    expected_version: int | None = Field(default=None, ge=1)


class ProcessingStandardPreviewRequest(BaseModel):
    activation_rule: str = Field(min_length=3, max_length=2000)
    filter_rule: str = Field(min_length=3, max_length=2000)
    priority: int = Field(default=100, ge=0, le=10000)


class SaveProcessingStandardRequest(ProcessingStandardPreviewRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    expected_version: int | None = Field(default=None, ge=1)


class ProcessingStandardDetailResponse(ProfileOptionResponse):
    profile_type: Literal["standard"] = "standard"
    activation_rule: str
    filter_rule: str
    priority: int


@router.get("/filter-profiles", response_model=list[ProfileOptionResponse])
async def list_filter_profiles(session: SessionDep, settings: SettingsDep):
    return [_option(row) for row in await ManagedProfileService(session, settings).list("filter")]


@router.get("/beautify-profiles", response_model=list[ProfileOptionResponse])
async def list_beautify_profiles(session: SessionDep, settings: SettingsDep):
    return [_option(row) for row in await ManagedProfileService(session, settings).list("beautify")]


@router.get("/completion-profiles", response_model=list[ProfileOptionResponse])
async def list_completion_profiles(session: SessionDep, settings: SettingsDep):
    return [
        _option(row)
        for row in await ManagedProfileService(session, settings).list("completion")
    ]


@router.get("/processing-standards", response_model=list[ProfileOptionResponse])
async def list_processing_standards(session: SessionDep, settings: SettingsDep):
    return [_option(row) for row in await ManagedProfileService(session, settings).list("standard")]


@router.post("/processing-standards/preview", response_model=ProfilePreviewResponse)
async def preview_processing_standard(
    payload: ProcessingStandardPreviewRequest,
    session: SessionDep,
    settings: SettingsDep,
):
    result = ManagedProfileService(session, settings).compile_standard(**payload.model_dump())
    return ProfilePreviewResponse(
        description=result.description,
        config=result.config,
        unsupported=result.unsupported,
        can_save=True,
    )


@router.post(
    "/processing-standards",
    response_model=ProcessingStandardDetailResponse,
    status_code=201,
)
async def create_processing_standard(
    payload: SaveProcessingStandardRequest,
    session: SessionDep,
    settings: SettingsDep,
):
    config = _standard_config(payload)
    try:
        row = await ManagedProfileService(session, settings).create(
            "standard",
            name=payload.name,
            instruction=payload.activation_rule,
            description=payload.description,
            config=config,
        )
    except ManagedProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _standard_detail(row)


@router.get(
    "/processing-standards/{profile_id}",
    response_model=ProcessingStandardDetailResponse,
)
async def get_processing_standard(
    profile_id: str, session: SessionDep, settings: SettingsDep
):
    row = await ManagedProfileService(session, settings).get("standard", profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="条件处理标准不存在")
    return _standard_detail(row)


@router.put(
    "/processing-standards/{profile_id}",
    response_model=ProcessingStandardDetailResponse,
)
async def update_processing_standard(
    profile_id: str,
    payload: SaveProcessingStandardRequest,
    session: SessionDep,
    settings: SettingsDep,
):
    if payload.expected_version is None:
        raise HTTPException(status_code=422, detail="缺少标准版本")
    try:
        row = await ManagedProfileService(session, settings).update(
            "standard",
            profile_id,
            expected_version=payload.expected_version,
            name=payload.name,
            instruction=payload.activation_rule,
            description=payload.description,
            config=_standard_config(payload),
        )
    except ManagedProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _standard_detail(row)


@router.delete("/processing-standards/{profile_id}", status_code=204)
async def delete_processing_standard(
    profile_id: str, session: SessionDep, settings: SettingsDep
):
    await _archive("standard", profile_id, session, settings)
    return Response(status_code=204)


@router.post("/filter-profiles/preview", response_model=ProfilePreviewResponse)
async def preview_filter_profile(payload: ProfilePreviewRequest, session: SessionDep, settings: SettingsDep):
    return await _preview("filter", payload, session, settings)


@router.post("/beautify-profiles/preview", response_model=ProfilePreviewResponse)
async def preview_beautify_profile(payload: ProfilePreviewRequest, session: SessionDep, settings: SettingsDep):
    return await _preview("beautify", payload, session, settings)


@router.post("/completion-profiles/preview", response_model=ProfilePreviewResponse)
async def preview_completion_profile(
    payload: ProfilePreviewRequest, session: SessionDep, settings: SettingsDep
):
    return await _preview("completion", payload, session, settings)


@router.post("/filter-profiles", response_model=ProfileDetailResponse, status_code=201)
async def create_filter_profile(payload: SaveProfileRequest, session: SessionDep, settings: SettingsDep):
    return await _create("filter", payload, session, settings)


@router.post("/beautify-profiles", response_model=ProfileDetailResponse, status_code=201)
async def create_beautify_profile(payload: SaveProfileRequest, session: SessionDep, settings: SettingsDep):
    return await _create("beautify", payload, session, settings)


@router.post("/completion-profiles", response_model=ProfileDetailResponse, status_code=201)
async def create_completion_profile(
    payload: SaveProfileRequest, session: SessionDep, settings: SettingsDep
):
    return await _create("completion", payload, session, settings)


@router.get("/filter-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def get_filter_profile(profile_id: str, session: SessionDep, settings: SettingsDep):
    return await _get("filter", profile_id, session, settings)


@router.get("/beautify-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def get_beautify_profile(profile_id: str, session: SessionDep, settings: SettingsDep):
    return await _get("beautify", profile_id, session, settings)


@router.get("/completion-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def get_completion_profile(
    profile_id: str, session: SessionDep, settings: SettingsDep
):
    return await _get("completion", profile_id, session, settings)


@router.put("/filter-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def update_filter_profile(profile_id: str, payload: SaveProfileRequest, session: SessionDep, settings: SettingsDep):
    return await _update("filter", profile_id, payload, session, settings)


@router.put("/beautify-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def update_beautify_profile(profile_id: str, payload: SaveProfileRequest, session: SessionDep, settings: SettingsDep):
    return await _update("beautify", profile_id, payload, session, settings)


@router.put("/completion-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def update_completion_profile(
    profile_id: str,
    payload: SaveProfileRequest,
    session: SessionDep,
    settings: SettingsDep,
):
    return await _update("completion", profile_id, payload, session, settings)


@router.delete("/filter-profiles/{profile_id}", status_code=204)
async def delete_filter_profile(profile_id: str, session: SessionDep, settings: SettingsDep):
    await _archive("filter", profile_id, session, settings)
    return Response(status_code=204)


@router.delete("/beautify-profiles/{profile_id}", status_code=204)
async def delete_beautify_profile(profile_id: str, session: SessionDep, settings: SettingsDep):
    await _archive("beautify", profile_id, session, settings)
    return Response(status_code=204)


@router.delete("/completion-profiles/{profile_id}", status_code=204)
async def delete_completion_profile(
    profile_id: str, session: SessionDep, settings: SettingsDep
):
    await _archive("completion", profile_id, session, settings)
    return Response(status_code=204)


@router.get("/similarity-profiles", response_model=list[ProfileOptionResponse])
async def list_similarity_profiles(settings: SettingsDep):
    profiles = [
        profile
        for profile in ProfileLoader(settings).list_similarity_profiles()
        if profile.id == "library_similarity_v2"
    ]
    names = {
        "library_similarity_v2": "素材库智能匹配",
    }
    return [
        ProfileOptionResponse(
            id=item.id,
            name=names.get(item.id, item.id),
            description=item.description,
            editable=False,
        )
        for item in profiles
    ]


async def _preview(profile_type: ProfileType, payload, session, settings):
    try:
        result = await ManagedProfileService(session, settings).compile(
            profile_type, payload.instruction
        )
    except ManagedProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProfilePreviewResponse(
        description=result.description,
        config=result.config,
        unsupported=result.unsupported,
        can_save=True,
    )


async def _create(profile_type: ProfileType, payload, session, settings):
    try:
        row = await ManagedProfileService(session, settings).create(
            profile_type,
            name=payload.name,
            instruction=payload.instruction,
            description=payload.description,
            config=payload.config,
        )
    except ManagedProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(row)


async def _get(profile_type: ProfileType, profile_id, session, settings):
    row = await ManagedProfileService(session, settings).get(profile_type, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="标准不存在")
    return _detail(row)


async def _update(profile_type: ProfileType, profile_id, payload, session, settings):
    if payload.expected_version is None:
        raise HTTPException(status_code=422, detail="缺少标准版本")
    try:
        row = await ManagedProfileService(session, settings).update(
            profile_type,
            profile_id,
            expected_version=payload.expected_version,
            name=payload.name,
            instruction=payload.instruction,
            description=payload.description,
            config=payload.config,
        )
    except ManagedProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(row)


async def _archive(profile_type: ProfileType, profile_id, session, settings):
    try:
        await ManagedProfileService(session, settings).archive(profile_type, profile_id)
    except ManagedProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _option(row: ProcessingProfile) -> ProfileOptionResponse:
    return ProfileOptionResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        version=row.version,
        status=row.status,
    )


def _detail(row: ProcessingProfile) -> ProfileDetailResponse:
    return ProfileDetailResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        version=row.version,
        status=row.status,
        editable=True,
        profile_type=row.profile_type,
        instruction=row.instruction,
        config=row.config_json,
    )


def _standard_config(payload: ProcessingStandardPreviewRequest) -> dict[str, object]:
    return {
        "activation_rule": payload.activation_rule.strip(),
        "filter_rule": payload.filter_rule.strip(),
        "priority": payload.priority,
    }


def _standard_detail(row: ProcessingProfile) -> ProcessingStandardDetailResponse:
    standard = ProcessingStandard.model_validate(row.config_json)
    return ProcessingStandardDetailResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        version=row.version,
        status=row.status,
        activation_rule=standard.activation_rule,
        filter_rule=standard.filter_rule,
        priority=standard.priority,
    )
