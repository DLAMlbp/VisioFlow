# Image Intelligence Service

多图智能筛选与自动美化服务。

当前实现为装修工匠照片处理 MVP。

## 已实现接口

```text
GET  /health
GET  /health/ready
POST /api/v1/uploads/presign
POST /api/v1/uploads/presign-download
POST /api/v1/uploads/presign-download-batch
POST /api/v1/upload-batches
POST /api/v1/upload-batches/{batch_id}/complete
POST /api/v1/image/jobs
GET  /api/v1/image/jobs/{job_id}
GET  /api/v1/image/jobs/{job_id}/results
POST /api/v1/image/jobs/{job_id}/cancel
POST /api/v1/image/jobs/{job_id}/images/{image_id}/retry
POST /api/v1/integration/jobs
GET  /api/v1/integration/jobs/{job_id}
GET  /api/v1/integration/jobs/{job_id}/results
```

## 第三方平台接入

第三方平台的小批量兼容接入可继续使用以下接口：

```text
POST /api/v1/integration/jobs
Content-Type: multipart/form-data
X-API-Key: <服务端 API Key>
```

必填字段为 `files`，可同时传 1 至 50 个 `image/jpeg`、`image/png` 或 `image/webp` 文件。可选表单字段为 `filter_profile`、`beautify_profile`、`enhance_level`（0-2）、`max_selected` 和 `callback_url`。成功后立即返回 `201` 及 `job_id`，图片在后台异步筛选和美化。

```bash
curl -X POST http://127.0.0.1:18000/api/v1/integration/jobs \
  -H "X-API-Key: <服务端 API Key>" \
  -F "files=@before.jpg;type=image/jpeg" \
  -F "files=@after.jpg;type=image/jpeg" \
  -F "max_selected=10"
```

使用 `GET /api/v1/integration/jobs/{job_id}` 轮询处理进度；完成后使用 `GET /api/v1/integration/jobs/{job_id}/results` 获取筛选决定、中文淘汰原因、质量指标及美化图片的对象键。所有调用必须由对方平台的服务端发起，不能在浏览器或 App 中暴露 `X-API-Key`。

50 至 500 张的大批量任务使用 `POST /api/v1/upload-batches` 一次登记文件，再将文件并发直传对象存储，最后调用 `POST /api/v1/upload-batches/{batch_id}/complete`。任务内部每 25 张分片投递，用户仍只看到一个任务。结果接口支持 `limit`、`offset` 和 `decision`，默认每页 50 张。

创建任务后的预处理 Worker 会读取原图，校验 Magic Bytes 与实际解码结果，提取尺寸、真实 MIME、文件大小、SHA256，并上传最长边 768px 的 JPEG 缩略图。元数据回写到 `image_items`，不新增对外 API。

Hard Filter 使用缩略图快速淘汰尺寸异常、极端模糊、极端过曝/欠曝与纯色图片。被淘汰的图片会记录 `reject_codes` 并提前结束，不进入后续质量检测或 AI 分析。

通过 Hard Filter 的图片会继续计算清晰度、曝光、对比度和噪声评分，全部归一化为 0-100，并写入 `image_metrics`。随后仅对保留图片执行自然增强，输出 JPEG 存储在 `enhanced/` 前缀下。

自然美化完成后，系统会向 AI 视觉模型发送最长边 1024px 的最终交付图副本，生成与业务标签无关的场景特征；随后使用 OpenCLIP 和 pgvector 检索素材库中的相似参考图片。高置信度结果继承参考图片的完整标签路径，中置信度进入复核，低置信度返回“未识别到相似的图片素材”。匹配失败不影响图片保留和交付。

参考素材通过“素材库”页面单独上传。标签树支持任意层级，上传时必须选择最末一级标签。素材原图不会进入普通图片任务，不执行清晰度、曝光、尺寸等质量过滤，也不会生成美化图；系统只进行格式校验、方向归一化、去重、场景分析和向量生成。

素材库接口包括：

```text
GET   /api/v1/library/tag-tree
POST  /api/v1/library/tag-nodes
PATCH /api/v1/library/tag-nodes/{node_id}
DELETE /api/v1/library/tag-nodes/{node_id}
POST  /api/v1/library/assets
GET   /api/v1/library/assets
PATCH /api/v1/library/assets/{asset_id}
POST  /api/v1/library/assets/{asset_id}/reindex
GET   /api/v1/tag-reviews
POST  /api/v1/tag-reviews/{image_id}/decision
```

当前匹配权重和阈值位于 `profiles/tags/library_similarity_v2.yaml`；`v1` 保留为初始基线。后续校准应继续新增配置版本，不直接覆盖历史版本。

筛选与美化参数位于 `profiles/filters/renovation_submission_v1.yaml` 和 `profiles/beautify/renovation_natural_v1.yaml`，可直接编辑后重启 Worker 生效。

## 本地启动

```bash
docker compose -p image-intelligence up -d
docker compose -p image-intelligence exec -T api alembic upgrade head
cd frontend
npm run dev -- --port 5174 --strictPort
```

本地 API 地址为 `http://127.0.0.1:18000`，MinIO 预签名上传地址为 `http://127.0.0.1:19000`。前端通过 Vite 代理调用 API，不会将服务端 API Key 暴露给浏览器。

默认队列并发为：`control=1`、`preprocess=4`、`enhance=2`、`analysis=4`、`embedding=1`、`library=1`、`cleanup=1`。`celery-beat` 定期清理 30 天前的业务图片、24 小时未提交的上传对象，并重新投递超过 15 分钟没有进展的任务。素材库原图和向量不参与自动清理。

服务器部署时通过环境变量调整 Worker 数量和 `INFERENCE_DEVICE=auto`。CPU 服务器保持单个 embedding Worker；GPU 服务器可让 OpenCLIP 自动使用 CUDA。Luna 默认全局限制为 24 次/分钟、4 并发，遇到 429、超时和 5xx 会自动退避。

所有 `/api/v1/*` 接口均需要 `X-API-Key` 请求头。请在本地 `.env` 或部署环境中设置高强度的 `API_KEY`，并仅由调用平台的服务端保存和发送该密钥。不要设置 `VITE_API_KEY` 或将服务密钥发送到浏览器。`/health` 提供存活检查，`/health/ready` 同时检查数据库、Redis 和对象存储；两者均不需要鉴权。

对象存储上传完成后，Worker 会读取对象实际大小；大于 `MAX_IMAGE_SIZE_MB` 的文件会在下载和解码前被拒绝。

API 文档：

```text
http://127.0.0.1:18000/docs
```

## 测试

```bash
.venv\Scripts\python -m pytest
```

## 数据库迁移

```bash
.venv\Scripts\python -m alembic upgrade head
```
