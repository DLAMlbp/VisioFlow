from functools import lru_cache

from src.core.config import Settings, get_settings
from src.services.storage.interfaces import StorageProvider
from src.services.storage.minio import MinIOStorageProvider


@lru_cache
def _build_storage_provider() -> StorageProvider:
    return MinIOStorageProvider(get_settings())


def get_storage_provider() -> StorageProvider:
    return _build_storage_provider()


def build_storage_provider(settings: Settings) -> StorageProvider:
    return MinIOStorageProvider(settings)
