from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from pathlib import Path


def _fetch(profile: Path, manifest: dict[str, object], prefix: str) -> Path:
    target = profile / str(manifest[f"{prefix}_model"])
    expected = str(manifest[f"{prefix}_sha256"]).lower()
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target
    temporary = target.with_suffix(".download")
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        urllib.request.urlopen(str(manifest[f"{prefix}_source"]), timeout=120) as response,
        temporary.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)
    actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if actual != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"{prefix} checksum mismatch: {actual}")
    temporary.replace(target)
    return target


def main() -> int:
    profile = Path("models/watermarks/dangjia/v1")
    manifest = json.loads((profile / "manifest.json").read_text(encoding="utf-8"))
    for prefix in ("lama", "migan"):
        print(_fetch(profile, manifest, prefix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
