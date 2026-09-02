# 私有 Redaction 数据集契约

此目录只保存本说明，真实客户图片、标注和配对样本不得提交 Git。

少量人工验收样图可放入 `incoming/`，并创建 `expectations.json`：

```json
{
  "images": [
    {
      "file": "sample.jpg",
      "boxes": [[100, 200, 300, 260]],
      "preserve_text": ["不应被遮挡的文案"]
    }
  ]
}
```

运行 `scripts/verify_logo_sample_acceptance.py` 会以 IoU 0.5 验证检出、误检、框外修改、遮挡后品牌 OCR 和普通文案保留。该门禁用于真实案例回归，不替代下述 300 实例统计验证集。

批量图片可先生成低阈值 OCR 候选框，减少人工从零画框的工作量：

```powershell
.venv\Scripts\python -m scripts.prelabel_dangjia_logo_dataset `
  E:\private\approved-images `
  E:\private\dangjia-prelabels.json `
  --capture-batch batch-20260902
```

输出固定标记为 `prelabels_not_training_ready`，所有框和负样本必须人工复核后才能进入训练/验证 JSON；脚本不会自动生成授权声明，也不会把自动候选当作真值。

## Logo 数据集

目录结构必须为：

```text
logo-dataset/
  dataset-manifest.json
  annotations/
    instances_train.json
    instances_val.json
  train2017/
  val2017/
```

COCO 类别固定为：

1. `dangjia_logo`：衣服、背景布、桌布、工牌等需要遮挡的实体 Logo。
2. `dangjia_product_logo`：产品包装上的小 Logo，用于实现 `include_product_logos` 开关。

每个 COCO `images[]` 项必须额外包含 `capture_batch`，同一连拍或同一现场批次只能进入 train 或 val 一侧。验证集必须包含无 Logo 的普通中文标题负样本。

`dataset-manifest.json` 至少包含：

```json
{
  "id": "dangjia-logo-dataset-v1",
  "version": 1,
  "authorized_for_training": true,
  "authorization_record": "内部授权记录编号或位置",
  "created_at": "2026-09-02",
  "notes": "数据来源和脱敏说明"
}
```

运行校验：

```powershell
.venv\Scripts\python -m scripts.validate_dangjia_dataset test-data/redaction-private/logo-dataset
```

默认至少要求 300 个标注实例、训练/验证批次完全隔离、图片内容无重复、框不越界且授权字段为真。

## 水印验证集

建议结构为 `watermark/paired`、`watermark/positive`、`watermark/negative`，至少 50 张正样本、20 组去水印前后配对样本，并记录授权来源。使用 `scripts.benchmark_redaction` 批量运行；人工残留/背景破坏标签应另存为不含个人文字内容的 JSON。
