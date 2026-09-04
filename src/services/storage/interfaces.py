import asyncio
from abc import ABC, abstractmethod


class StorageProvider(ABC):
    async def healthcheck(self) -> None:
        """Raise when the storage backend is unavailable."""

    @abstractmethod
    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        pass

    @abstractmethod
    async def download(self, object_key: str) -> bytes:
        pass

    @abstractmethod
    async def get_size(self, object_key: str) -> int:
        pass

    @abstractmethod
    async def delete(self, object_key: str) -> None:
        pass

    async def delete_many(self, object_keys: list[str]) -> set[str]:
        """Delete object keys and return the keys that could not be removed."""
        unique_keys = list(dict.fromkeys(object_keys))
        results = await asyncio.gather(
            *(self.delete(object_key) for object_key in unique_keys),
            return_exceptions=True,
        )
        return {
            object_key
            for object_key, result in zip(unique_keys, results, strict=True)
            if isinstance(result, BaseException)
        }

    @abstractmethod
    async def presign_upload(
        self,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> str:
        pass

    @abstractmethod
    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        pass
