from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PAGE_SIZE = 200
TAG_SEPARATOR = "\x1f"


class ApiClient:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
        retries: int = 3,
    ) -> Any:
        body = data
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"
        for attempt in range(retries):
            try:
                request = urllib.request.Request(url, data=body, headers=headers, method=method)
                with urllib.request.urlopen(request, timeout=120) as response:
                    content = response.read()
                    if not content:
                        return None
                    response_type = response.headers.get_content_type()
                    if response_type == "application/json":
                        return json.loads(content.decode("utf-8"))
                    return content
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                if attempt + 1 == retries:
                    raise
                time.sleep(2**attempt)
        raise AssertionError("request retry loop did not return")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)

    def put_bytes(self, url: str, data: bytes, content_type: str) -> None:
        self.request("PUT", url, data=data, content_type=content_type)


def load_env_value(path: Path, name: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    raise RuntimeError(f"{path} does not define {name}")


def tag_key(tags: list[str]) -> str:
    return TAG_SEPARATOR.join(tag.strip().casefold() for tag in tags)


def list_all_assets(client: ApiClient) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    total = 0
    while not assets or len(assets) < total:
        query = urllib.parse.urlencode({"limit": PAGE_SIZE, "offset": len(assets)})
        page = client.get(f"/api/v1/library/assets?{query}")
        total = int(page["total"])
        items = list(page["items"])
        assets.extend(items)
        if not items:
            break
    if len(assets) != total:
        raise RuntimeError(f"素材分页读取不完整：期望 {total}，实际 {len(assets)}")
    return assets


def content_type_for(asset: dict[str, Any]) -> str:
    declared = str(asset.get("content_type") or "").lower()
    if declared in {"image/jpeg", "image/png", "image/webp"}:
        return declared
    filename = str(asset.get("original_filename") or asset["original_object_key"])
    guessed = mimetypes.guess_type(filename)[0]
    return guessed if guessed in {"image/jpeg", "image/png", "image/webp"} else "image/jpeg"


def sync_standards(source: ApiClient, destination: ApiClient, *, dry_run: bool) -> tuple[int, int]:
    source_options = source.get("/api/v1/processing-standards")
    source_standards = [
        source.get(f"/api/v1/processing-standards/{item['id']}")
        for item in source_options
    ]
    destination_options = destination.get("/api/v1/processing-standards")
    by_id = {item["id"]: item for item in destination_options}
    by_name = {item["name"]: item for item in destination_options}
    created = 0
    updated = 0

    ordered = sorted(source_standards, key=lambda item: bool(item["is_fallback"]))
    for standard in ordered:
        existing = by_id.get(standard["id"]) or by_name.get(standard["name"])
        payload = {
            "name": standard["name"],
            "classification_rule": standard["classification_rule"],
            "filter_rule": standard["filter_rule"],
            "priority": standard["priority"],
            "is_fallback": standard["is_fallback"],
            "description": standard["description"],
        }
        if existing:
            detail = destination.get(f"/api/v1/processing-standards/{existing['id']}")
            comparable_fields = (
                "name",
                "classification_rule",
                "filter_rule",
                "priority",
                "is_fallback",
                "description",
            )
            if all(detail.get(field) == payload[field] for field in comparable_fields):
                continue
            if dry_run:
                print(f"[dry-run] 更新过滤标准：{standard['name']}")
            else:
                payload["expected_version"] = detail["version"]
                destination.request(
                    "PUT", f"/api/v1/processing-standards/{existing['id']}", payload
                )
            updated += 1
        else:
            if dry_run:
                print(f"[dry-run] 新增过滤标准：{standard['name']}")
            else:
                created_standard = destination.post("/api/v1/processing-standards", payload)
                by_id[created_standard["id"]] = created_standard
                by_name[created_standard["name"]] = created_standard
            created += 1
    return created, updated


def sync_library(source: ApiClient, destination: ApiClient, *, dry_run: bool) -> tuple[int, int]:
    source_groups = source.get("/api/v1/library/groups")
    destination_groups = destination.get("/api/v1/library/groups")
    destination_by_tags = {tag_key(group["tags"]): group for group in destination_groups}
    created_groups = 0

    for group in source_groups:
        key = tag_key(group["tags"])
        if key in destination_by_tags:
            continue
        if dry_run:
            print(f"[dry-run] 新增素材组：{' / '.join(group['tags'])}")
            destination_by_tags[key] = {"id": f"dry-run-{created_groups}", **group}
        else:
            created = destination.post(
                "/api/v1/library/groups",
                {"tags": group["tags"], "sort_order": group["sort_order"]},
            )
            if group["status"] == "disabled":
                created = destination.request(
                    "PATCH",
                    f"/api/v1/library/groups/{created['id']}",
                    {"status": "disabled"},
                )
            destination_by_tags[key] = created
        created_groups += 1

    source_group_keys = {group["id"]: tag_key(group["tags"]) for group in source_groups}
    destination_group_keys = {
        group["id"]: tag_key(group["tags"])
        for group in destination.get("/api/v1/library/groups")
    }
    source_assets = list_all_assets(source)
    destination_assets = list_all_assets(destination)
    existing_files = {
        (destination_group_keys[asset["group_id"]], asset.get("original_filename") or "")
        for asset in destination_assets
    }
    missing_assets = [
        asset
        for asset in source_assets
        if (
            source_group_keys[asset["group_id"]],
            asset.get("original_filename") or "",
        )
        not in existing_files
    ]
    if dry_run:
        for asset in missing_assets:
            print(f"[dry-run] 新增素材：{asset.get('original_filename') or asset['id']}")
        return created_groups, len(missing_assets)

    for index, asset in enumerate(missing_assets, start=1):
        source_download = source.post(
            "/api/v1/uploads/presign-download",
            {"object_key": asset["original_object_key"]},
        )
        image_bytes = source.request("GET", source_download["download_url"])
        mime_type = content_type_for(asset)
        filename = asset.get("original_filename") or Path(asset["original_object_key"]).name
        destination_upload = destination.post(
            "/api/v1/uploads/presign",
            {
                "filename": filename,
                "content_type": mime_type,
                "file_size": len(image_bytes),
            },
        )
        destination.put_bytes(
            destination_upload["upload_url"], image_bytes, mime_type
        )
        group = destination_by_tags[source_group_keys[asset["group_id"]]]
        destination.post(
            "/api/v1/library/assets",
            {
                "object_key": destination_upload["object_key"],
                "group_id": group["id"],
                "original_filename": filename,
            },
        )
        print(f"素材同步 {index}/{len(missing_assets)}：{filename}")
    return created_groups, len(missing_assets)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读远端并增量同步标准与素材到本地")
    parser.add_argument("--source", default="http://47.111.188.85:8088")
    parser.add_argument("--destination", default="http://127.0.0.1:18000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    destination_key = load_env_value(args.env_file, "API_KEY")
    source = ApiClient(args.source)
    destination = ApiClient(args.destination, destination_key)
    standards_created, standards_updated = sync_standards(
        source, destination, dry_run=args.dry_run
    )
    groups_created, assets_created = sync_library(
        source, destination, dry_run=args.dry_run
    )
    print(
        "同步完成："
        f"标准新增 {standards_created}、更新 {standards_updated}；"
        f"素材组新增 {groups_created}；素材新增 {assets_created}。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
