from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, model_validator


class IntegrationUrlImage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_key: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("objectKey", "object_key"),
    )
    image_url: HttpUrl = Field(
        validation_alias=AliasChoices("imageUrl", "image_url"),
    )


class IntegrationUrlJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    notify_url: HttpUrl = Field(
        validation_alias=AliasChoices("notifyUrl", "callback_url", "notify_url"),
    )
    images: list[IntegrationUrlImage] = Field(min_length=1, max_length=50)
    completion_profile: str | None = Field(default=None, min_length=1, max_length=80)
    completed_filter_profile: str | None = Field(default=None, min_length=1, max_length=80)
    non_completed_filter_profile: str | None = Field(default=None, min_length=1, max_length=80)
    beautify_profile: str | None = Field(default=None, min_length=1, max_length=80)
    redaction_profile: str | None = Field(default=None, min_length=1, max_length=80)
    similarity_profile: str = Field(default="library_similarity_v2", min_length=1, max_length=80)
    enhance_level: int = Field(default=1, ge=0, le=2)
    max_selected: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def require_unique_object_keys(self):
        object_keys = [image.object_key for image in self.images]
        if len(set(object_keys)) != len(object_keys):
            raise ValueError("images[].objectKey 在同一任务内不能重复")
        return self


class IntegrationCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    job_id: str
    status: str
    total: int
    ok: bool = True
    code: str = ""
    message: str = ""
    task_id: str = Field(serialization_alias="taskId")
