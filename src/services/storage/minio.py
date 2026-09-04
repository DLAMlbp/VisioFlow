import asyncio
from functools import cached_property
from pathlib import PurePosixPath
from urllib.parse import quote

import boto3
from botocore.client import Config

from src.core.config import Settings
from src.services.storage.interfaces import StorageProvider
from src.services.storage.keys import validate_object_key


class MinIOStorageProvider(StorageProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @cached_property
    def client(self):
        return self._build_client(self.settings.s3_endpoint)

    @cached_property
    def public_client(self):
        return self._build_client(self.settings.s3_public_endpoint or self.settings.s3_endpoint)

    def _build_client(self, endpoint_url: str):
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=3,
                read_timeout=20,
                retries={"total_max_attempts": 2, "mode": "standard"},
                tcp_keepalive=True,
            ),
        )

    async def healthcheck(self) -> None:
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.settings.s3_bucket)

    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        validate_object_key(object_key)
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.settings.s3_bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )

    async def download(self, object_key: str) -> bytes:
        validate_object_key(object_key)
        response = await asyncio.to_thread(
            self.client.get_object,
            Bucket=self.settings.s3_bucket,
            Key=object_key,
        )
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def get_size(self, object_key: str) -> int:
        validate_object_key(object_key)
        response = await asyncio.to_thread(
            self.client.head_object,
            Bucket=self.settings.s3_bucket,
            Key=object_key,
        )
        return int(response["ContentLength"])

    async def delete(self, object_key: str) -> None:
        validate_object_key(object_key)
        await asyncio.to_thread(
            self.client.delete_object,
            Bucket=self.settings.s3_bucket,
            Key=object_key,
        )

    async def presign_upload(
        self,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> str:
        validate_object_key(object_key)
        return await asyncio.to_thread(
            self.public_client.generate_presigned_url,
            "put_object",
            Params={
                "Bucket": self.settings.s3_bucket,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_seconds,
        )

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        validate_object_key(object_key)
        filename = PurePosixPath(object_key).name
        return await asyncio.to_thread(
            self.public_client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.settings.s3_bucket,
                "Key": object_key,
                "ResponseContentDisposition": (
                    f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
                ),
            },
            ExpiresIn=expires_seconds,
        )
