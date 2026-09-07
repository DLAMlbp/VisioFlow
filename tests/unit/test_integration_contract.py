from src.main import app


def test_v1_company_api_contract_supports_callback_first_delivery() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    create = paths["/api/v1/integration/jobs"]["post"]
    file_create = paths["/api/v1/integration/file-jobs"]["post"]
    partner_create = paths["/api/app/image/filter-requests"]["post"]
    progress = paths["/api/v1/integration/jobs/{job_id}"]["get"]
    results = paths["/api/v1/integration/jobs/{job_id}/results"]["get"]

    assert "application/json" in create["requestBody"]["content"]
    assert "multipart/form-data" in file_create["requestBody"]["content"]
    assert "application/json" in partner_create["requestBody"]["content"]
    assert "201" in create["responses"]
    assert "200" in progress["responses"]
    assert "200" in results["responses"]

    for operation in (create, file_create, partner_create, progress, results):
        parameters = {item["name"].lower(): item for item in operation.get("parameters", [])}
        assert parameters["x-api-key"]["in"] == "header"

    multipart_schema = file_create["requestBody"]["content"]["multipart/form-data"]["schema"]
    if "$ref" in multipart_schema:
        multipart_schema = schema["components"]["schemas"][multipart_schema["$ref"].split("/")[-1]]
    assert "callback_url" in multipart_schema["required"]

    result_schema = schema["components"]["schemas"]["IntegrationJobResultsResponse"]
    result_fields = result_schema["properties"]
    assert {
        "job_id",
        "total",
        "selected",
        "rejected",
        "not_selected",
        "result_total",
        "limit",
        "offset",
        "download_expires_in",
        "images",
    } <= result_fields.keys()

    image_schema = schema["components"]["schemas"]["IntegrationImageResultResponse"]
    image_fields = image_schema["properties"]
    assert {
        "image_id",
        "client_object_key",
        "decision",
        "score",
        "original_object_key",
        "enhanced_object_key",
        "original_url",
        "enhanced_url",
        "files_expired",
        "reject_codes",
        "reasons",
        "metrics",
        "enhanced_metrics",
        "ai_tags",
        "tagging_result",
    } <= image_fields.keys()


def test_public_integration_response_fields_are_exactly_stable() -> None:
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["IntegrationCreateResponse"]["properties"]) == {
        "code",
        "job_id",
        "message",
        "ok",
        "status",
        "taskId",
        "total",
    }
    assert set(schemas["ImageJobProgressResponse"]["properties"]) == {
        "failure",
        "job_id",
        "not_selected",
        "processed",
        "progress",
        "rejected",
        "selected",
        "stage_counts",
        "status",
        "tagging",
        "total",
    }
    assert set(schemas["IntegrationJobResultsResponse"]["properties"]) == {
        "download_expires_in",
        "images",
        "job_id",
        "limit",
        "not_selected",
        "offset",
        "rejected",
        "result_total",
        "selected",
        "total",
    }
    assert set(schemas["IntegrationImageResultResponse"]["properties"]) == {
        "activation_reason",
        "ai_tags",
        "analysis_status",
        "audit_dimensions",
        "beautify",
        "beautify_status",
        "classification",
        "classification_status",
        "client_object_key",
        "completion",
        "decision",
        "embedding_status",
        "enhanced_metrics",
        "enhanced_object_key",
        "enhanced_preview_object_key",
        "enhanced_url",
        "files_expired",
        "filter_status",
        "image_id",
        "library_tags",
        "match_status",
        "metrics",
        "original_object_key",
        "original_preview_object_key",
        "original_url",
        "pipeline_stage",
        "processing_standard_id",
        "processing_standard_name",
        "reasons",
        "reject_codes",
        "routed_filter_profile_id",
        "routed_filter_profile_version",
        "score",
        "tagging_result",
    }
