# 水印去除与 Logo 马赛克开发计划

## 执行状态（2026-09-02）

当前分支：`shuiyin`

已完成：

- 两项能力已作为美化 Profile 的独立开关接入；默认关闭，并保留旧任务快照兼容性。
- 去水印严格限定在左下角归一化 ROI `[0, 0.84, 0.48, 1]`，真实示例验证 `outside_roi_changed_pixels = 0`，中央“长沙靠谱施工队”保持不变。
- 生产主路径采用 ROI-only RapidOCR + OpenCV 官方 LaMa ONNX；保留 Alpha 反混合、MI-GAN 和 Telea 作为适配/回退能力。
- Logo 路径已实现“可选 YOLOX ONNX 单类模型 + RapidOCR 当家品牌文字回退 + 强像素马赛克”，普通中央文案不会因 OCR 全图文字而被打码。
- Worker 已按“方向归一化 → 去水印 → 美化 → Logo 马赛克”接入，失败时安全降级并写入结构化审计和指标。
- 前端已增加两个独立开关、结果区域框/置信度预览，以及删除、拖拽、缩放、手绘和保存重绘的人工复核；API、类型、任务审计和生产运行手册已同步。
- 开源依赖的版本、提交、许可证、适配方式和模型 SHA-256 已记录在 `THIRD_PARTY_NOTICES.md` 与 manifest；模型不提交 Git，由校验脚本获取。
- 后端完整测试、Ruff、前端测试与构建、Compose 配置检查、生产 Docker 镜像构建和容器内真实样图冒烟均已通过。
- 实测 8 张负载并发 1：P50 约 5.28 秒、含冷启动 P95 约 30.31 秒、峰值 RSS 1452.54 MiB；并发 2 峰值达 2000.91 MiB，触及 2 GiB 上限约 97.7%，因此增强 Worker 保持并发 1、内存 2 GiB、CPU 1.5。1536×2048 冷启动样本也已通过，峰值 RSS 1276.31 MiB。
- 已增加 12,000,000 解码像素硬上限并完成 1080×1440、1536×2048、3000×4000 三档回归；高分辨率地址尾部星号已通过“缩放笔画核 + 第二行笔画检测走廊”修复，三档均为 ROI 外变化 0，12MP 峰值 RSS 1395.60 MiB。
- 阶段 2B 根据实测采用 RapidOCR + OpenVINO 轻量路径，而非完整 PaddleOCR 运行时；OCR 仅接收左下角 ROI。为避免全尺寸中间图跨队列存取，当前在 2 GiB、并发 1 的 `enhance` Worker 内执行；没有创建无实际收益的空 `redaction` 队列。
- 用户重新提供的图二、图三已完成真实样图验收：衣服 Logo 1/1 检出（0.9335），背景布和桌布 2/2 检出（均 1.0）；框外变化像素均为 0，遮挡后 OCR 不再识别出品牌文字，祝福语与人物保持不变。

尚未宣称完成的验收项：

- 尚未取得计划要求的私有固定验证集（至少 50 张水印图、300 个实体 Logo 实例），因此不能凭单张成功案例宣称已达到 95% 召回率、98% 精确率及批量残留率指标。
- YOLOX 生产权重依赖上述授权训练集；当前没有伪造模型指标，运行时会使用已经可工作的 OCR 品牌回退。取得数据后可直接复用现有 YOLOX 训练、ONNX 导出和推理适配链路。
- 已提供可直接执行的数据授权/COCO/批次隔离校验、锁定上游提交的 YOLOX-Nano 训练与 ONNX 导出脚本，以及按 IoU 0.5 计算精确率、召回率和负样本误检的独立验收脚本；缺少的仅是授权数据与由其训练出的权重本身。

## 1. 目标与范围

在现有“图片美化”流水线中增加两项彼此独立、可配置、可审计的能力：

1. 仅去除左下角“当家 APP / 地址 / 时间”叠加水印。
2. 检测图片中真实场景里的“当家 APP”Logo，并对 Logo 区域打强马赛克。

明确不做：

- 不删除图片中央或其他位置的普通标题、施工文案和祝福语。
- 不把全图 OCR 识别到的文字都当成水印。
- 第一阶段不接第三方在线图片编辑接口。
- 第一阶段不处理任意品牌 Logo，只处理 `dangjia_logo` 单一类别。
- 未确认许可前，不把 GPL、无明确许可证或非商业模型代码/权重合入生产项目。

## 2. 总体技术路线

```text
原图
  -> EXIF 方向归一化
  -> 左下角硬 ROI 定位
  -> Alpha 反混合去水印
  -> OpenCV 局部修补兜底
  -> 现有自然美化
  -> YOLOX ONNX 检测实体 Logo
  -> 检测框外扩并打强马赛克
  -> 质量复检、记录审计数据
  -> JPEG 单次最终输出
```

关键约束：

- 去水印阶段只能修改归一化坐标 `[0, 0.84, 0.48, 1]` 内的像素。
- 中央文案不在去水印搜索和修改范围内。
- Logo 马赛克必须在锐化、美化之后执行，避免后续增强重新暴露 Logo。
- 去水印失败或 Logo 检测模型不可用时，不得让整个图片任务失败；保留安全的上一步结果并写入审计信息。
- 功能默认关闭，通过美化 Profile 显式开启。

### 2.1 开源复用优先原则

本功能不重复实现已有的通用算法。开发时遵循以下顺序：

1. 上游项目提供稳定 Python API：锁定版本后直接作为依赖调用。
2. 上游只提供 CLI：优先做独立适配器或 Sidecar，避免复制整个项目。
3. 上游 API 不稳定、但许可证允许：只 vendoring 必需的最小模块，保留原文件版权头，并在 `THIRD_PARTY_NOTICES.md` 记录仓库、提交 SHA、许可证和本地修改。
4. 上游语言或依赖与本项目不兼容：复用论文公式、算法流程和测试思路，在 Python 中实现薄适配；仍按许可证要求保留来源说明。
5. GPL、无明确许可证、仅限非商业使用的代码和权重，不进入生产代码或生产镜像，只允许在隔离的本地基准环境中评估。

计划直接复用如下：

| 能力 | 上游仓库 | 复用方式 | 本项目只开发的部分 |
|---|---|---|---|
| ROI 擦除、OpenCV/MI-GAN/LaMa 后端 | `wiltodelta/remove-ai-watermarks`，Apache-2.0 | 优先锁定版本直接导入；无稳定 API 时 vendoring 最小后端模块 | “当家 APP”检测器、ROI 保护和审计适配 |
| 半透明水印反混合 | `allenk/GeminiWatermarkTool`，MIT | 复用 Alpha 反混合公式、模板相关匹配、残留修补流程；按本项目 Python 技术栈做薄适配 | “当家 APP”Alpha/前景色模板和分辨率 Profile |
| 变化文字定位 | `PaddlePaddle/PaddleOCR`，Apache-2.0 | 直接使用官方 OCR Pipeline 或轻量部署接口 | 强制 ROI 裁剪、OCR 多边形到笔画掩膜的细化规则 |
| Logo 训练和 ONNX 推理 | `Megvii-BaseDetection/YOLOX`，Apache-2.0 | 直接使用官方训练、导出脚本及 ONNX Runtime 预处理/后处理代码 | 单类别数据集、权重、业务阈值和马赛克策略 |
| 像素马赛克、Telea 修补 | 当前依赖 OpenCV | 直接调用 `cv2.resize`、`cv2.inpaint`，不自行实现图像算子 | 框外扩、保护边界和参数配置 |
| LaMa 效果基准 | `Sanster/IOPaint` / `advimman/lama`，Apache-2.0 | 作为隔离的本地服务或基准工具使用 | 仅在 MI-GAN/Telea 不达标时增加生产适配 |

不直接合入：

- `Moonshine-Image` 为 GPL-3.0，只作产品流程和效果参考。
- `WDNet`、`SLBR` 及部分 Logo 数据集缺少足够清晰的生产许可，只作研究基准。
- IOPaint 的完整依赖与当前 FastAPI、Pillow 版本存在冲突，不直接安装进现有 API/Worker 镜像。

所有第三方版本必须锁定到 tag 或 commit SHA，不跟随默认分支漂移；引入前运行依赖漏洞和许可证扫描。

## 3. 推荐模块划分

新增文件：

```text
src/services/images/redaction.py              # 总入口和处理结果模型
src/services/images/watermark.py              # ROI、模板定位、Alpha 反混合、修补
src/services/images/logo_detector.py          # YOLOX ONNX 推理与后处理
src/services/images/mosaic.py                 # 像素化马赛克
src/services/images/redaction_acceptance.py   # 越界修改、残留和马赛克验收
tests/unit/test_watermark.py
tests/unit/test_logo_detector.py
tests/unit/test_mosaic.py
tests/unit/test_redaction_pipeline.py
tests/fixtures/redaction/README.md
models/watermarks/dangjia/v1/manifest.json
models/logos/dangjia/v1/manifest.json
src/services/images/adapters/remove_ai_watermarks.py
src/services/images/adapters/paddleocr.py
src/services/images/adapters/yolox_onnx.py
THIRD_PARTY_NOTICES.md
```

模型权重不提交 Git。`manifest.json` 只保存版本、下载地址或对象存储 Key、SHA-256、输入尺寸、类别和许可证信息。

## 4. 配置与数据契约

### 4.1 美化 Profile

在 `src/services/profiles.py` 增加嵌套配置，不增加数据库字段，继续利用现有 `beautify_profile_snapshot` 固化任务配置：

```python
class WatermarkRemovalConfig(BaseModel):
    enabled: bool = False
    mode: Literal["dangjia_bottom_left"] = "dangjia_bottom_left"
    roi: tuple[float, float, float, float] = (0.0, 0.84, 0.48, 1.0)
    backend: Literal["deblend", "deblend_then_telea"] = "deblend_then_telea"
    preserve_outside_roi: bool = True
    max_modified_ratio: float = 0.10


class LogoMosaicConfig(BaseModel):
    enabled: bool = False
    targets: list[str] = ["dangjia_logo"]
    confidence: float = 0.45
    nms_iou: float = 0.50
    box_expansion: float = 0.12
    mosaic_block_ratio: float = 0.08
    include_product_logos: bool = False
```

在 `BeautifyProfile` 增加：

```python
watermark_removal: WatermarkRemovalConfig = WatermarkRemovalConfig()
logo_mosaic: LogoMosaicConfig = LogoMosaicConfig()
```

注意：`neutralize_beautify_profile()` 只能中和亮度、对比度等视觉参数，不能清空这两项合规处理配置。Worker 必须在中和美化参数前保存 Redaction 配置。

### 4.2 审计数据

第一版复用 `ImageResult.enhancement_audit_json`，不做数据库迁移：

```json
{
  "redaction": {
    "watermark": {
      "enabled": true,
      "status": "applied",
      "profile_version": "dangjia_watermark_v1",
      "roi_px": [0, 1210, 518, 1440],
      "mask_area_ratio": 0.031,
      "outside_roi_changed_pixels": 0,
      "fallback": "telea"
    },
    "logos": {
      "enabled": true,
      "status": "applied",
      "model_version": "dangjia_yolox_nano_v1",
      "detections": 2,
      "boxes": [[430, 920, 790, 1100]],
      "inference_ms": 146
    }
  }
}
```

不在审计中保存完整图片、模型密钥或用户隐私文字内容。

## 5. 分阶段开发任务

## 阶段 0：样本集和效果基线

目标：先建立可重复评估的数据集，避免凭单张图片调参数。

任务：

- [ ] 建立不提交真实客户原图的本地数据目录 `test-data/redaction-private/`，加入 `.gitignore`。
- [ ] 收集至少 50 张左下角水印图，覆盖人物、墙体、木材、管线、明暗背景和不同 JPEG 压缩质量。
- [ ] 尽量收集 20 组“加水印前/后”的同图配对样本，用于估计 Alpha 和前景色。
- [ ] 收集至少 300 个实体“当家 APP”Logo 实例，覆盖衣服褶皱、背景布、桌布、工牌、远近、旋转、遮挡和模糊。
- [ ] 固定验证集，训练集、验证集按拍摄批次隔离，禁止同一连拍同时落入两侧。
- [ ] 使用当前三张示例建立最小脱敏回归夹具或只提交裁剪后的非敏感区域。
- [ ] 编写 `scripts/benchmark_redaction.py`，输出处理耗时、峰值内存、掩膜面积、ROI 外变化像素数和结果目录。
- [ ] 对 `remove-ai-watermarks`、`GeminiWatermarkTool`、PaddleOCR、YOLOX 分别锁定一个 tag/commit SHA，并记录许可证。
- [ ] 建立 `THIRD_PARTY_NOTICES.md`，记录直接依赖、vendoring 文件、算法移植和模型权重来源。
- [ ] 编写最小复用 Spike，验证 `remove-ai-watermarks` 能否作为 Python API 调用；只有 API 不稳定时才 vendoring 最小实现。

完成标准：

- 数据说明、来源授权、是否允许用于训练均有记录。
- 基准脚本可对一个目录批量执行，失败图片不会中断整批。

建议提交：`test(redaction): add private dataset contract and benchmark harness`

## 阶段 1：配置、接口和纯 OpenCV 骨架

目标：在不引入 OCR/ONNX 模型的情况下打通完整链路。

任务：

- [ ] 在 `src/services/profiles.py` 增加两组嵌套配置和范围校验。
- [ ] 更新 `src/services/managed_profiles.py` 的中性 Profile 和快照兼容逻辑。
- [ ] 新增 `RedactionResult`、`WatermarkResult`、`LogoMosaicResult` 数据类。
- [ ] 新增 ROI 归一化坐标转像素坐标函数，并对越界、横竖图和小图做校验。
- [ ] 实现 `mosaic.py`：检测框外扩、裁边、像素化、最小块大小和重叠框合并。
- [ ] 在 `src/workers/enhance.py` 中接入空实现：去水印在美化前，马赛克在美化后。
- [ ] 将 Redaction 审计合并到现有 `enhancement_audit_json`。
- [ ] 功能关闭时输出结果必须与当前主分支一致。

必须先写的测试：

- [ ] 默认配置关闭，两项能力不执行。
- [ ] ROI 坐标转换在 1080×1440 时符合预期。
- [ ] 去水印模块不能修改 ROI 外任何像素。
- [ ] 马赛克只修改外扩后的检测框。
- [ ] 空检测、越界检测框、重复检测框不会报错。
- [ ] Redaction 异常时 Worker 保留安全结果并写 `status=skipped/failed_safe`。

完成标准：

- 所有旧测试通过。
- 新骨架在功能关闭时无数据库迁移、无额外模型加载、无性能回退。

建议提交：`feat(redaction): add profile contract and safe processing skeleton`

## 阶段 2：左下角水印去除 V1

目标：高质量处理固定位置、半透明的“当家 APP / 地址 / 时间”水印，并严格保留中央文字。

任务：

- [ ] 先接入 `remove-ai-watermarks` 的区域擦除和 OpenCV 后端适配器，不重新实现后端调度。
- [ ] 从 `GeminiWatermarkTool` 复用 Alpha 反混合、模板相关匹配和残留清理算法结构，保留 MIT 来源声明。
- [ ] 使用配对样本估计固定第一行“当家 APP”的前景色和 Alpha 蒙版。
- [ ] 为不同输出尺寸/比例建立水印 Profile，至少覆盖 1080×1440；其他尺寸按相对坐标缩放并重新对齐。
- [ ] 使用归一化互相关或边缘模板在 ROI 内完成小范围平移/缩放对齐。
- [ ] 实现 Alpha 反混合，加入分母下限和 RGB 截断，避免噪声放大。
- [ ] 地址、时间行第一版使用固定行带 + 灰度/颜色/连通域约束生成候选掩膜。
- [ ] 对反混合后的残留掩膜使用 `cv2.inpaint(..., cv2.INPAINT_TELEA)`，修补半径从 2～4 像素按分辨率缩放。
- [ ] 掩膜边缘做轻微膨胀和羽化，但禁止把整块 OCR 矩形直接作为掩膜。
- [ ] 记录模板置信度、掩膜占比、是否使用 Telea 兜底。
- [ ] 置信度不足或掩膜占比超过上限时跳过处理，不进行大面积猜测性修复。

算法验收：

- [ ] 去水印阶段 ROI 外变化像素数严格等于 0。
- [ ] 示例图中央“长沙靠谱施工队”区域在去水印阶段逐像素不变。
- [ ] 50 张验证集中，明显水印残留率不高于 5%。
- [ ] 50 张验证集中，明显背景破坏率不高于 3%。
- [ ] 无水印图片误处理率不高于 1%。

建议提交：`feat(redaction): remove fixed dangjia watermark inside protected ROI`

## 阶段 2B：变化文字 OCR 增强（条件执行）

只有阶段 2 对地址和时间的召回率不足时才执行本阶段。

任务：

- [ ] 直接接入官方 PaddleOCR Pipeline；只有完整 Paddle 运行时超出资源预算时，才切换官方轻量部署/ONNX 路径。
- [ ] 对 PaddleOCR 做离线效果、许可、内存和冷启动基准，不自行开发文字检测模型。
- [ ] OCR 输入只裁剪左下角 ROI，禁止把全图 OCR 结果用于删除。
- [ ] OCR 仅提供文字多边形；再通过像素特征细化到笔画级掩膜。
- [ ] 将 OCR 运行放到独立 `redaction` Worker，避免挤占当前 768MB `enhance` Worker。
- [ ] OCR/模型不可用时自动回到阶段 2 的规则方案。

进入下一阶段的门槛：

- OCR 方案在固定验证集上明显优于规则方案。
- 单进程峰值内存和 P95 耗时已记录，并能在目标机器资源预算内运行。

建议提交：`feat(redaction): refine variable watermark text with ROI-only OCR`

## 阶段 3：实体 Logo 检测与自动马赛克

目标：对衣服、背景布、桌布等实体载体上的“当家 APP”Logo 自动打码，不处理普通文案。

离线模型任务：

- [ ] 直接使用 YOLOX 官方训练配置、数据加载器、增强和 ONNX 导出脚本，不复制开发训练框架。
- [ ] 使用矩形框标注单类别 `dangjia_logo`。
- [ ] 单独标记产品包装小 Logo，以便实现 `include_product_logos` 开关。
- [ ] 训练 YOLOX-Nano；Nano 召回不足时再评估 YOLOX-Tiny。
- [ ] 使用旋转、透视、亮度、压缩、模糊、遮挡和颜色扰动增强。
- [ ] 导出 ONNX，固定输入尺寸、归一化方式、类别顺序、NMS 参数和 opset。
- [ ] 将权重上传到内部对象存储或镜像构建上下文，记录 SHA-256 和训练数据版本。

代码任务：

- [ ] 在可选依赖中增加 `onnxruntime`，不要引入完整 PyTorch 训练环境到生产镜像。
- [ ] 从 YOLOX 官方 ONNXRuntime Demo 复用 Letterbox、归一化、坐标还原和 NMS 逻辑，通过 `adapters/yolox_onnx.py` 隔离上游代码。
- [ ] `logo_detector.py` 只负责进程级懒加载、线程数限制、业务阈值和检测结果模型。
- [ ] 每张图片只加载一次模型，模型加载失败熔断并输出明确审计状态。
- [ ] 检测框外扩 10%～15%，使用强像素马赛克，块大小按短边比例计算。
- [ ] 马赛克后禁止再次执行锐化、局部清晰度或 AI 修复。
- [ ] 前端预览返回检测框，允许用户取消误检框和补画漏检框；自动批处理仍可无需人工完成。

模型验收：

- [ ] 独立验证集 Logo 检测召回率不低于 95%。
- [ ] 精确率不低于 98%，普通中文标题不得被当作 Logo。
- [ ] 图二衣服 Logo、图三背景布和桌布 Logo 均能检出。
- [ ] 图一中央文字和左下角以外普通施工文字不打码。
- [ ] 经 200% 放大后，马赛克区域不可辨认原 Logo 文字和图标细节。

建议提交：`feat(redaction): detect dangjia logos with ONNX and apply strong mosaic`

## 阶段 4：前端控制与人工兜底

目标：让功能可控，不把模型判断当作绝对正确。

任务：

- [ ] 在美化 Profile 编辑界面增加“去除左下角水印”和“当家 Logo 马赛克”开关。
- [ ] 显示简明说明：中央文案不会被当作水印处理。
- [ ] 增加处理后预览覆盖层，显示水印 ROI、Logo 检测框和置信度。
- [ ] 支持删除、拖拽、缩放检测框，并支持手动画框。
- [ ] 对低置信度、掩膜过大、模型不可用显示告警，不伪装为成功。
- [ ] API 类型同步到 `frontend/src/types.ts` 和 `frontend/src/services/api.ts`。

完成标准：

- 用户可以只开去水印、只开 Logo 马赛克、两者都开或都关。
- 人工修正不会改变原任务记录，使用新处理版本重新生成结果并留审计记录。

建议提交：`feat(frontend): add redaction controls and detection review overlay`

## 阶段 5：性能、回归和发布准备

任务：

- [ ] 对 1080×1440、2048 长边和最大允许尺寸分别压测。
- [ ] 记录单图 P50/P95、峰值 RSS、模型冷启动、并发 1/2 下吞吐。
- [ ] 为 Redaction 增加指标：执行数、跳过数、失败安全数、耗时、检测数和模型版本。
- [ ] 在 `tests/unit/test_celery_worker_roles.py` 增加 `redaction` Worker 角色测试。
- [ ] 在 `src/workers/celery_app.py` 注册独立队列时，确保其他 Worker 不加载 ONNX 模型。
- [ ] 在 `docker-compose.yml` 增加本地 `worker-redaction`。
- [ ] 只有基准证明内存足够后才修改 `docker-compose.prod.yml`；初始建议并发 1，内存从 1.5～2GB 实测起步。
- [ ] 更新 README、联调文档、生产 Runbook、模型回滚步骤和功能开关说明。
- [ ] 执行许可证清单，保存第三方代码和模型权重的来源、版本及许可证。
- [ ] CI 校验 `THIRD_PARTY_NOTICES.md`、模型 manifest 和实际依赖版本一致。

本地质量门禁：

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
npm --prefix frontend test
npm --prefix frontend run build
```

发布门禁：

- [ ] 功能默认关闭发布，先仅对内部测试 Profile 开启。
- [ ] 使用固定版本号、Git SHA 或镜像 digest，不使用 `latest`。
- [ ] 在本地或 CI 测试并构建镜像，服务器只拉取版本化镜像。
- [ ] 部署前执行服务器资源、磁盘、inode、负载和容器状态检查。
- [ ] 先灰度 5%，再提升到 25%、50%、100%；每阶段检查误删、漏检、失败安全率和耗时。
- [ ] 回滚只需关闭 Profile 开关或切回上一版本镜像，不依赖删除数据。

建议提交：`chore(redaction): add observability deployment config and runbook`

## 6. Worker 集成的具体改造点

`src/workers/enhance.py` 建议改造成以下顺序：

```python
original_bytes = await storage.download(item.object_key)

# 1. 方向归一化
oriented_bytes = beautify_service.normalize_orientation(...).image_bytes

# 2. 不受 AI 美化“是否需要”影响的固定去水印
pre_result = redaction_service.remove_watermark(
    oriented_bytes,
    redaction_config.watermark_removal,
)
beautify_input = pre_result.image_bytes

# 3. 现有美化，验收失败时退回 beautify_input，而不是退回带水印原图
beautified_bytes = run_existing_beautify_or_fallback(beautify_input)

# 4. 最后执行 Logo 马赛克
post_result = redaction_service.mosaic_logos(
    beautified_bytes,
    redaction_config.logo_mosaic,
)
enhanced_bytes = post_result.image_bytes

# 5. 合并审计、上传和后续质量检测
```

必须修正的回退语义：

- 美化验收失败：回退到“已去水印但未美化”的图。
- 去水印失败：使用方向归一化图继续美化，并记录 `failed_safe`。
- Logo 模型失败：交付已去水印和美化的图，并记录 `failed_safe`，不得输出虚假的 `applied`。
- 最终上传失败：继续沿用现有任务重试机制。

## 7. 测试矩阵

| 场景 | 预期结果 |
|---|---|
| 图一左下角有水印、中央有大字 | 只去左下角；中央文字保留 |
| 无水印、有中央标题 | 不修改任何文字区域 |
| 左下角背景是人物 | 水印消失，人体轮廓无明显断裂 |
| 左下角背景是木条/管线 | 纹理连续，无大块糊斑 |
| 图二衣服有 Logo | Logo 全覆盖马赛克，衣服其他区域保留 |
| 图三背景布和桌布有 Logo | 两处均打码，祝福语保留 |
| 普通中文文字与 Logo 相似 | 不打码 |
| Logo 旋转、遮挡、褶皱 | 仍能检测，框覆盖完整 |
| Logo 在图片边缘 | 框裁边正确，不越界 |
| 模型文件缺失或损坏 | 图片任务继续，审计为 `failed_safe` |
| 功能开关关闭 | 行为与当前主分支一致 |

## 8. 首轮开发的停止条件

以下任一情况出现时，不继续扩大上线范围：

- ROI 外发生了水印模块造成的像素修改。
- 中央或其他普通文案出现误删。
- Logo 误检导致人物面部、普通文字或施工主体被大面积打码。
- 单进程内存超过已分配容器上限的 80%。
- 模型或数据许可证无法确认可用于当前业务。
- 固定验证集尚未通过，或结果只能靠挑选成功案例证明。

## 9. 推荐执行顺序

严格按以下顺序推进：

1. 完成阶段 0 数据与基准工具。
2. 完成阶段 1 安全骨架，确保关闭功能时零回归。
3. 先交付阶段 2 的左下角水印能力。
4. 根据验证结果决定是否实施阶段 2B OCR。
5. 完成阶段 3 单类别 Logo 检测和马赛克。
6. 完成阶段 4 人工兜底。
7. 完成阶段 5 压测、灰度和发布准备。

第一轮可交付范围建议限定为：1080×1440 竖图、左下角“当家 APP”水印、实体“当家 APP”Logo。验证稳定后再扩展分辨率、横图和其他品牌。
