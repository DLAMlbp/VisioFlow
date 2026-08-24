from uuid import uuid4


def build_job_id() -> str:
    return f"job_{uuid4().hex}"


def build_image_id() -> str:
    return f"img_{uuid4().hex}"
