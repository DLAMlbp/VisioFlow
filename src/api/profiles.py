from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.core.config import Settings, get_settings
from src.services.profiles import ProfileLoader

router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]


class ProfileOptionResponse(BaseModel):
    id: str
    name: str
    description: str


@router.get("/filter-profiles", response_model=list[ProfileOptionResponse])
async def list_filter_profiles(settings: SettingsDep) -> list[ProfileOptionResponse]:
    profiles = ProfileLoader(settings).list_filter_profiles()
    return [
        ProfileOptionResponse(
            id=profile.id,
            name="装修照片基础筛选" if profile.id == "renovation_submission_v1" else profile.id,
            description=profile.description,
        )
        for profile in profiles
    ]


@router.get("/beautify-profiles", response_model=list[ProfileOptionResponse])
async def list_beautify_profiles(settings: SettingsDep) -> list[ProfileOptionResponse]:
    profiles = ProfileLoader(settings).list_beautify_profiles()
    return [
        ProfileOptionResponse(
            id=profile.id,
            name="装修照片自然美化" if profile.id == "renovation_natural_v1" else profile.id,
            description=profile.description,
        )
        for profile in profiles
    ]


@router.get("/similarity-profiles", response_model=list[ProfileOptionResponse])
async def list_similarity_profiles(settings: SettingsDep) -> list[ProfileOptionResponse]:
    profiles = ProfileLoader(settings).list_similarity_profiles()
    names = {
        "library_similarity_v2": "装修场景智能匹配（推荐）",
        "library_similarity_v1": "装修场景严格匹配",
    }
    return [
        ProfileOptionResponse(
            id=profile.id,
            name=names.get(profile.id, profile.id),
            description=profile.description,
        )
        for profile in profiles
    ]
