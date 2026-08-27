from uuid import uuid4


def build_job_id() -> str:
    return f"job_{uuid4().hex}"


def build_image_id() -> str:
    return f"img_{uuid4().hex}"


def build_upload_batch_id() -> str:
    return f"bat_{uuid4().hex}"


def build_upload_batch_item_id() -> str:
    return f"ubi_{uuid4().hex}"
