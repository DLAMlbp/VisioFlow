from pydantic import BaseModel, Field


class PresignedUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    file_size: int = Field(gt=0)


class PresignedUploadResponse(BaseModel):
    object_key: str
    upload_url: str


class PresignedDownloadRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)


class PresignedDownloadResponse(BaseModel):
    object_key: str
    download_url: str
