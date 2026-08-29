from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_allowed_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:5174,http://localhost:5174,"
        "http://127.0.0.1:5175,http://localhost:5175"
    )
    api_key: str = ""
    integration_api_key: str = ""
    integration_max_files: int = 50
    callback_timeout_seconds: int = 15
    callback_max_attempts: int = 5
    callback_retry_base_seconds: int = 5
    callback_recovery_interval_seconds: int = 5
    callback_delivery_lease_seconds: int = 120
    callback_recovery_batch_size: int = 100

    database_url: str = "postgresql+asyncpg://user:password@postgres:5432/image_ai"
    redis_url: str = "redis://redis:6379/0"

    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str | None = None
    s3_access_key: str = "minio"
    s3_secret_key: str = "minio123"
    s3_bucket: str = "image-ai"
    s3_region: str = "us-east-1"
    s3_presign_expires_seconds: int = 900

    max_images_per_job: int = 500
    max_upload_batch_size: int = 500
    upload_batch_expiry_hours: int = 24
    upload_batch_presign_expires_seconds: int = 3600
    job_dispatch_chunk_size: int = 25
    max_image_size_mb: int = 25
    allowed_image_content_types: set[str] = Field(
        default_factory=lambda: {"image/jpeg", "image/png", "image/webp"}
    )

    thumbnail_long_side: int = 768
    hard_filter_max_width: int = 10000
    hard_filter_max_height: int = 10000
    technical_duplicate_hamming_distance: int = 5
    quality_sharpness_reference: float = 250.0
    quality_contrast_min_stddev: float = 10.0
    quality_contrast_max_stddev: float = 64.0
    quality_exposure_brightness_penalty: float = 25.0
    quality_exposure_clipping_penalty: float = 45.0
    quality_noise_penalty: float = 1.5
    profiles_directory: str = "profiles"
    image_retention_days: int = 30

    ai_tagging_enabled: bool = True
    ai_tagging_provider: str = "openai"
    ai_tagging_base_url: Literal["https://router.keenlight.ai/v1"] = (
        "https://router.keenlight.ai/v1"
    )
    ai_tagging_model: str = "gpt-5.6-sol"
    ai_tagging_api_key: str = ""
    ai_tagging_timeout_seconds: int = 30
    ai_tagging_max_retries: int = 2
    ai_tagging_image_long_side: int = 1024
    ai_tagging_concurrency: int = 4
    ai_tagging_rate_limit_per_minute: int = 24
    ai_tagging_store_raw_response: bool = False

    image_embedding_model: str = "ViT-B-32"
    image_embedding_pretrained: str = "laion2b_s34b_b79k"
    image_embedding_version: str = "openclip_vit_b32_v1"
    inference_device: str = "auto"
    inference_cpu_threads: int = 4
    default_similarity_profile: str = "library_similarity_v2"
    cleanup_interval_seconds: int = 3600
    pipeline_recovery_interval_seconds: int = 300
    pipeline_stale_seconds: int = 900



@lru_cache
def get_settings() -> Settings:
    return Settings()
