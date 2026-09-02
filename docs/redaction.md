# 水印去除与 Logo 马赛克

## 产品边界

- 仅对有权处理的图片使用本功能。
- 水印模式只处理左下角 `dangjia_bottom_left` 区域，不扫描全图文字。
- Logo 默认只匹配“当家 APP”品牌锁定，普通施工标题和开工祝福语不处理。
- 产品包装小 Logo 默认不属于遮挡范围；只有经标注的 YOLOX 模型能稳定区分该类别。

## 处理顺序

1. EXIF 方向归一化。
2. 左下角硬 ROI 中使用 RapidOCR 定位三行拍摄信息。
3. 有校准 Alpha 模板时优先反向混合；否则使用 OpenCV LaMa 修复，MI-GAN/Telea 只做失败降级。
4. 执行原有自然美化；美化验收失败时回退到“已去水印”的输入。
5. 最后检测 Logo 并打不可逆像素马赛克，避免后续锐化重新暴露细节。

任意第三方模型失败都转为 `failed_safe`，当前图像仍可交付，不得伪造 `applied` 审计结果。

## Profile 配置

```json
{
  "watermark_removal": {
    "enabled": true,
    "mode": "dangjia_bottom_left",
    "backend": "deblend_then_lama",
    "roi": [0.0, 0.84, 0.48, 1.0],
    "preserve_outside_roi": true
  },
  "logo_mosaic": {
    "enabled": true,
    "targets": ["dangjia_logo"],
    "confidence": 0.45,
    "box_expansion": 0.08,
    "mosaic_block_ratio": 0.16,
    "include_product_logos": false
  }
}
```

管理界面可分别开启两个开关。结果详情中的“处理区域”可显示水印 ROI、Logo 检测框和置信度。

## 人工复核

Logo 马赛克开启时，Worker 会额外保存一份私有的“已去水印、已美化、尚未打 Logo 马赛克”基底图。结果详情中的“复核 Logo 框”支持：

- 删除自动误检框；
- 拖动检测框；
- 使用右下角控制点缩放；
- 手动画出漏检框；
- 保存空框集合以恢复全部自动马赛克区域。

保存时系统始终从基底图重新生成最终 JPEG，不会尝试在已像素化的图片上反向恢复。接口为：

```http
PUT /api/v1/image/jobs/{job_id}/images/{image_id}/redaction/logos
Content-Type: application/json

{"boxes": [[x0, y0, x1, y1]]}
```

坐标是最终图片像素坐标，最多 100 个框；空框数组表示清除全部 Logo 马赛克。越界、负数或空面积框会被拒绝。审计状态变为 `manual_applied` 或 `manual_cleared`，并记录自动原始框、人工修订次数和最终框集合。基底图不返回给浏览器，并随任务文件一起按保留策略清理。

## 模型与开源复用

```powershell
.venv\Scripts\python -m scripts.fetch_redaction_models
```

下载脚本必须校验 manifest 中的 SHA-256 后才原子替换模型文件。代码仓库不提交 ONNX 权重；生产镜像由 CI 下载并固化，运行时保持离线。来源、版本、commit、许可证和复用范围见 `THIRD_PARTY_NOTICES.md`。

Logo 检测采用两级路由：

- 存在已校验 YOLOX ONNX 权重且推理正常时，只采信该模型结果，不混入 OCR 候选，以保持独立验证集测得的精确率成立。
- 没有权重或模型加载/推理失败时，使用 RapidOCR 匹配“当家 APP”，并向左/向下扩展覆盖图标与副标。

### YOLOX 训练与验收

训练环境与生产镜像隔离。先将官方 YOLOX 仓库检出到锁定提交 `6ddff4824372906469a7fae2dc3206c7aa4bbaee`，按 `test-data/redaction-private/README.md` 准备已授权 COCO 数据，然后执行：

```powershell
.\scripts\train_dangjia_logo.ps1 `
  -YoloXDirectory C:\path\to\YOLOX `
  -DatasetDirectory E:\private\dangjia-logo-dataset `
  -PythonExecutable C:\path\to\training-venv\Scripts\python.exe `
  -Fp16

.venv\Scripts\python -m scripts.evaluate_dangjia_logo `
  E:\private\dangjia-logo-dataset
```

训练脚本在启动前校验上游 Git SHA 和数据授权/分割契约，直接调用 YOLOX 官方 `tools/train.py`、`tools/export_onnx.py`，不会把 PyTorch 训练栈装入生产镜像。独立验证脚本以 IoU 0.5 计算两类 Logo 的 TP/FP/FN，默认门槛为召回率 95%、精确率 98%，且普通文字负样本不得产生误检。只有通过后才能把 Logo manifest 从 `awaiting-trained-weights` 改为启用状态并填写 ONNX SHA-256。

## 验收与性能

```powershell
.venv\Scripts\python -m scripts.benchmark_redaction <input> <output> --watermark --logo
```

报告包含单图耗时、进程 RSS、掩膜面积、ROI/Logo 框外变化像素数、检测框、置信度和模型版本。当前 1080×1440 参考图的实测结果：

### 用户真实 Logo 样图

2026-09-02 使用用户重新提供的两张 1080×1440 原图执行 OCR 回退路径验收：

| 样图 | 人工可见目标 | 自动检出 | 置信度 | 整图遮挡面积 | 框外变化像素 | 遮挡后品牌 OCR |
|---|---:|---:|---|---:|---:|---:|
| 施工人员衣服 | 1 | 1 | 0.9335 | 2.7661% | 0 | 0 |
| 背景布与桌布 | 2 | 2 | 1.0000、1.0000 | 3.0473% | 0 | 0 |

视觉复核确认衣服、背景布和桌布的品牌锁定均被强马赛克覆盖；“恭贺贵府”“开工大吉”等普通文案、人物和施工主体未被误打码。默认框外扩由 12% 收紧为 8%，马赛克块比例由 8% 提高到 16%；旋转衣服 Logo 使用 OCR 四边形的有向边长估算高度，避免轴对齐外接框造成大面积误伤。该结果证明三处指定样例通过，但不替代独立数据集上的 95% 召回率和 98% 精确率统计。

- 冷启动约 27.0 秒，热态约 5.2 秒。
- 峰值 RSS 约 1.27 GiB。
- 水印 ROI 外变化像素数为 0。

8 张重复负载只用于性能测量，不作为准确率样本。2026-09-02 的同机对照结果：

| 负载 | 成功 | 墙钟时间 | 吞吐 | 单图 P50 | 单图 P95（含冷启动） | 峰值 RSS |
|---|---:|---:|---:|---:|---:|---:|
| 并发 1，8×1080×1440 | 8/8 | 80.45 s | 0.099 张/s | 5.28 s | 30.31 s | 1452.54 MiB |
| 并发 2，8×1080×1440 | 8/8 | 66.07 s | 0.121 张/s | 6.33 s | 49.61 s | 2000.91 MiB |
| 并发 1，1×1536×2048 冷启动 | 1/1 | 31.13 s | — | — | — | 1276.31 MiB |

修正高分辨率地址尾部符号掩膜后，对同一参考图重新执行三档冷启动视觉回归：

| 尺寸 | 耗时 | 峰值 RSS | ROI 掩膜比例 | ROI 外变化像素 | 视觉结果 |
|---|---:|---:|---:|---:|---|
| 1080×1440 | 30.75 s | 1258.05 MiB | 45.65% | 0 | 水印移除，中央文案保留 |
| 1536×2048 | 30.68 s | 1276.84 MiB | 45.06% | 0 | 水印移除，中央文案保留 |
| 3000×4000（12MP） | 30.98 s | 1395.60 MiB | 46.14% | 0 | 含地址尾部星号全部移除 |

图片除了 25 MB 编码大小限制外，还受 `MAX_IMAGE_PIXELS` 控制，默认最多 12,000,000 个解码像素。该上限覆盖常见 3000×4000 手机照片，并在进入 OCR/LaMa 前从图片元数据拒绝超限输入，防止解压缩炸弹或 100MP 图片突破 2 GiB Worker 预算。

并发 2 仅提升约 22% 吞吐，却消耗 2 GiB 限额约 97.7%，超过“峰值不得高于容器上限 80%”的安全门槛，因此禁止用于当前生产配置。明细与汇总分别写入 `redaction-benchmark.json` 和 `redaction-benchmark-summary.json`。

因此生产 `worker-enhance` 固定并发 1、内存上限 2 GiB。当前将 OCR/LaMa 与美化放在同一单并发 Worker 中，可避免跨队列传输全尺寸中间图；如果后续实测并发需求要求拆分，再按相同 2 GiB 预算建立独立 `redaction` Worker。若目标机器不能稳定保留资源，应保持功能关闭，不应降低容器上限后强行启用。
