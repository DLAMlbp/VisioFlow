from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

EXPECTED_CLASSES = {"dangjia_logo", "dangjia_product_logo"}


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"JSON 根节点必须是对象：{path}")
    return payload


def _validate_split(dataset: Path, split: str) -> dict[str, object]:
    annotation_path = dataset / "annotations" / f"instances_{split}.json"
    image_directory = dataset / ("train2017" if split == "train" else "val2017")
    payload = _load_json(annotation_path)
    categories = payload.get("categories")
    images = payload.get("images")
    annotations = payload.get("annotations")
    if not all(isinstance(value, list) for value in (categories, images, annotations)):
        raise ValueError(f"{annotation_path} 缺少 COCO categories/images/annotations 数组")
    category_names = {str(item.get("name")) for item in categories if isinstance(item, dict)}
    if category_names != EXPECTED_CLASSES:
        raise ValueError(f"{split} 类别必须严格等于 {sorted(EXPECTED_CLASSES)}")
    category_ids = {int(item["id"]) for item in categories if isinstance(item, dict)}
    image_by_id: dict[int, dict[str, object]] = {}
    batches: set[str] = set()
    hashes: set[str] = set()
    for raw in images:
        if not isinstance(raw, dict):
            raise TypeError(f"{split} images 中存在非对象项")
        image_id = int(raw["id"])
        filename = str(raw["file_name"])
        batch = str(raw.get("capture_batch") or "").strip()
        if not batch:
            raise ValueError(f"{split}/{filename} 缺少 capture_batch")
        path = image_directory / filename
        if not path.is_file():
            raise ValueError(f"图片不存在：{path}")
        actual_hash = sha256(path.read_bytes()).hexdigest()
        if actual_hash in hashes:
            raise ValueError(f"{split} 内存在重复图片内容：{filename}")
        hashes.add(actual_hash)
        batches.add(batch)
        image_by_id[image_id] = raw
    class_counts = {name: 0 for name in EXPECTED_CLASSES}
    annotated_image_ids: set[int] = set()
    name_by_category = {
        int(item["id"]): str(item["name"])
        for item in categories
        if isinstance(item, dict)
    }
    for raw in annotations:
        if not isinstance(raw, dict):
            raise TypeError(f"{split} annotations 中存在非对象项")
        image = image_by_id.get(int(raw["image_id"]))
        category_id = int(raw["category_id"])
        if image is None or category_id not in category_ids:
            raise ValueError(f"{split} 标注引用了不存在的图片或类别")
        bbox = raw.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"{split} bbox 必须为 [x,y,width,height]")
        x, y, width, height = (float(value) for value in bbox)
        if min(x, y) < 0 or width <= 0 or height <= 0:
            raise ValueError(f"{split} 存在负坐标或空 bbox")
        if x + width > float(image["width"]) or y + height > float(image["height"]):
            raise ValueError(f"{split} bbox 超出图片边界")
        class_counts[name_by_category[category_id]] += 1
        annotated_image_ids.add(int(raw["image_id"]))
    return {
        "images": len(images),
        "annotations": len(annotations),
        "negative_images": len(images) - len(annotated_image_ids),
        "class_counts": class_counts,
        "batches": batches,
        "hashes": hashes,
    }


def validate_dataset(
    dataset: Path,
    *,
    min_instances: int = 300,
    min_class_instances: int = 10,
) -> dict[str, object]:
    manifest = _load_json(dataset / "dataset-manifest.json")
    if manifest.get("authorized_for_training") is not True:
        raise ValueError("dataset-manifest.json 未明确 authorized_for_training=true")
    if not str(manifest.get("authorization_record") or "").strip():
        raise ValueError("dataset-manifest.json 缺少 authorization_record")
    train = _validate_split(dataset, "train")
    val = _validate_split(dataset, "val")
    overlap_batches = train["batches"] & val["batches"]
    if overlap_batches:
        raise ValueError(f"训练/验证集 capture_batch 泄漏：{sorted(overlap_batches)[:5]}")
    overlap_hashes = train["hashes"] & val["hashes"]
    if overlap_hashes:
        raise ValueError("训练/验证集存在相同图片内容")
    total_instances = int(train["annotations"]) + int(val["annotations"])
    if total_instances < min_instances:
        raise ValueError(f"Logo 标注实例不足：{total_instances} < {min_instances}")
    total_class_counts = {
        name: int(train["class_counts"][name]) + int(val["class_counts"][name])
        for name in EXPECTED_CLASSES
    }
    insufficient = {
        name: count
        for name, count in total_class_counts.items()
        if count < min_class_instances
    }
    if insufficient:
        raise ValueError(
            f"类别实例不足（每类至少 {min_class_instances}）：{insufficient}"
        )
    if int(val["negative_images"]) < 1:
        raise ValueError("验证集必须包含至少一张无 Logo 的普通文字负样本")
    return {
        "dataset_id": manifest.get("id"),
        "dataset_version": manifest.get("version"),
        "authorized_for_training": True,
        "total_instances": total_instances,
        "total_class_counts": total_class_counts,
        "train": {key: value for key, value in train.items() if key not in {"batches", "hashes"}},
        "val": {key: value for key, value in val.items() if key not in {"batches", "hashes"}},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the private Dangjia COCO dataset")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--min-instances", type=int, default=300)
    parser.add_argument("--min-class-instances", type=int, default=10)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate_dataset(
        args.dataset,
        min_instances=args.min_instances,
        min_class_instances=args.min_class_instances,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
