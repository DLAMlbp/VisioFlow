from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _requirements(path: Path) -> list[Requirement]:
    return [
        Requirement(line.strip())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def verify(root: Path = Path(".")) -> dict[str, object]:
    requirements = _requirements(root / "requirements-redaction.txt")
    installed: dict[str, str] = {}
    for requirement in requirements:
        actual = version(requirement.name)
        if Version(actual) not in requirement.specifier:
            raise RuntimeError(
                f"{requirement.name}={actual} 不符合 {requirement.specifier}"
            )
        installed[requirement.name] = actual

    watermark_directory = root / "models" / "watermarks" / "dangjia" / "v1"
    watermark = json.loads(
        (watermark_directory / "manifest.json").read_text(encoding="utf-8")
    )
    model_hashes: dict[str, str] = {}
    for prefix in ("lama", "migan"):
        model = watermark_directory / str(watermark[f"{prefix}_model"])
        expected = str(watermark[f"{prefix}_sha256"]).lower()
        actual = _sha256(model)
        if actual != expected:
            raise RuntimeError(f"{prefix} 模型 SHA-256 与 manifest 不一致")
        model_hashes[prefix] = actual

    logo_directory = root / "models" / "logos" / "dangjia" / "v1"
    logo = json.loads((logo_directory / "manifest.json").read_text(encoding="utf-8"))
    if logo.get("source_commit") != "6ddff4824372906469a7fae2dc3206c7aa4bbaee":
        raise RuntimeError("YOLOX source_commit 未锁定到已审查提交")
    if logo.get("classes") != ["dangjia_logo", "dangjia_product_logo"]:
        raise RuntimeError("Logo manifest 类别顺序不符合训练/推理契约")
    if logo.get("status") not in {"awaiting-trained-weights", "active"}:
        raise RuntimeError("Logo manifest status 无效")
    if logo.get("status") == "active":
        model = logo_directory / str(logo["model"])
        expected = str(logo.get("sha256") or "").lower()
        if len(expected) != 64 or _sha256(model) != expected:
            raise RuntimeError("已启用 Logo 模型缺失或 SHA-256 不一致")

    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    required_notice_tokens = [
        "9bb4e9cd54879f7072f14be6560a4b7a54d7d7a8",
        "7c6a99f2c3df97eb3c430ef87a0c962aea5cb80e",
        "6ddff4824372906469a7fae2dc3206c7aa4bbaee",
        "0e629c8be05635035c01a829d10a91bbcd56a27a",
        str(watermark["lama_sha256"]),
    ]
    missing = [token for token in required_notice_tokens if token not in notices]
    if missing:
        raise RuntimeError(f"THIRD_PARTY_NOTICES.md 缺少锁定来源：{missing}")

    return {
        "status": "passed",
        "installed": installed,
        "watermark_model_hashes": model_hashes,
        "logo_model_status": logo["status"],
        "yolox_source_commit": logo["source_commit"],
    }


def main() -> int:
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
