from src.main import app


def test_v1_company_api_contract_remains_backward_compatible() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    create = paths["/api/v1/integration/jobs"]["post"]
    progress = paths["/api/v1/integration/jobs/{job_id}"]["get"]
    results = paths["/api/v1/integration/jobs/{job_id}/results"]["get"]

    assert "multipart/form-data" in create["requestBody"]["content"]
    assert "201" in create["responses"]
    assert "200" in progress["responses"]
    assert "200" in results["responses"]

    for operation in (create, progress, results):
        parameters = {item["name"].lower(): item for item in operation.get("parameters", [])}
        assert parameters["x-api-key"]["in"] == "header"

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
