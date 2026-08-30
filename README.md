# Image Intelligence Service

公司服务端接入说明见 [`docs/company-api.md`](docs/company-api.md)。交付包中的所有 API Key 均为空，必须由部署方自行配置。

生产发布、健康验证、回滚和备份要求见 [`docs/production-runbook.md`](docs/production-runbook.md)。

面向多行业的图片筛选、美化、内容分析与素材相似匹配服务。业务规则、标签体系和素材范围均由用户配置，不内置装修行业判断。

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

必填字段为 `files`、`callback_url`、`processing_standards` 和 `beautify_profile`；`files` 可同时传 1 至 50 个 `image/jpeg`、`image/png` 或 `image/webp` 文件。正式模式固定执行“原图一次 AI 识别 → 两套标准二选一 → 逐项过滤 → 独立美化 → 将首次识别标签绑定到美化图 → 素材匹配”，不会为同一任务对美化图再次调用视觉大模型。`processing_standards` 必须恰好包含两套互斥且完整覆盖的过滤标准 ID。历史阶段开关字段只为 v1 字段兼容而保留，传 `false` 会被拒绝；未命中或同时命中两套标准会明确标记为 AI 分类失败，不会静默放行。可选字段包括 `library_scope_node_id`、`similarity_profile`、`enhance_level` 和 `max_selected`。成功后立即返回 `201` 及 `job_id`，任务进入后台异步处理；进入终态后由 `control` Worker主动向 `callback_url` 推送结果。

```bash
curl -X POST http://127.0.0.1:18000/api/v1/integration/jobs \
  -H "X-API-Key: <服务端 API Key>" \
  -F "files=@product-front.jpg;type=image/jpeg" \
  -F "files=@product-side.jpg;type=image/jpeg" \
  -F "processing_standards=<条件标准 ID 1>,<条件标准 ID 2>" \
  -F "beautify_profile=<标准管理中的美化标准 ID>" \
  -F "callback_url=https://client.example.com/api/image-callback" \
  -F "max_selected=10"
```

任务进入 `completed`、`partial_failed`、`failed` 或 `cancelled` 后，服务会向 `callback_url` 发送 `POST application/json`。回调至少投递一次，接收方应按 `event_id` 或 `job_id + completed_at` 幂等处理，并校验 `X-Callback-Timestamp` 与 `X-Callback-Signature`；HTTP 2xx 表示接收成功。生产环境必须配置 `CALLBACK_ALLOWED_HOSTS` 和 `CALLBACK_SIGNING_SECRET`。`GET /api/v1/integration/jobs/{job_id}` 与 `/results` 保留为补偿和排障接口，不再要求客户端持续轮询。所有调用必须由对方平台的服务端发起，不能在浏览器或 App 中暴露 `X-API-Key`。

50 至 500 张的大批量任务使用 `POST /api/v1/upload-batches` 一次登记文件，再将文件并发直传对象存储，最后调用 `POST /api/v1/upload-batches/{batch_id}/complete`。任务内部每 25 张分片投递，用户仍只看到一个任务。结果接口支持 `limit`、`offset` 和 `decision`，默认每页 50 张。

生产 Web 网关对第三方集成接口按来源地址限制请求速率和并发连接；更大规模或多租户场景应在外部 API Gateway 中按调用方密钥配置独立配额。正式公网入口必须由负载均衡器或网关终止 HTTPS，Compose 暴露的 HTTP 端口不应直接对公网开放。

创建任务后的预处理 Worker 会读取原图，校验 Magic Bytes 与实际解码结果，提取尺寸、真实 MIME、文件大小、SHA256，并上传最长边 768px 的 JPEG 缩略图。元数据回写到 `image_items`，不新增对外 API。

本地预处理只拦截无法解码、超过系统资源上限或完全重复的文件，不再执行固定的业务过滤规则。

图片会计算清晰度、曝光、对比度和噪声评分，全部归一化为 0-100，并与真实尺寸一起交给视觉 AI。AI 按“标准管理”保存的用户自然语言决定过滤结果，并为每张图片生成一套完整美化参数；旧标准中保存的视觉参数不会参与新任务。AI 失败时任务明确失败并可重试，不会回退到内置业务标准。通过过滤的图片由本地安全执行器应用 AI 参数，输出 JPEG 存储在 `enhanced/` 前缀下。

需要相似匹配时，系统生成通用内容特征（主体、对象、属性、视觉特征和可见文字），并使用 OpenCLIP 和 pgvector 检索指定素材范围中的相似参考图片。素材分析结果按文件内容复用，不会在每个任务中重新分析素材库。高置信度结果继承参考图片的完整标签路径，中置信度进入复核，低置信度返回“未识别到相似的图片素材”。匹配失败不影响图片保留和交付。

参考素材通过“素材库”页面单独上传。标签树支持任意层级，上传时必须选择最末一级标签。素材原图不会进入普通图片任务，不执行清晰度、曝光、尺寸等质量过滤，也不会生成美化图；系统只进行格式校验、方向归一化、去重、通用内容分析和向量生成。

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

条件标准与美化标准分开管理。系统只允许启用两套条件过滤标准，每套只描述“何时启用”和“命中后如何筛选”；两套启动规则必须互斥且完整覆盖所有输入图片，每张图必须且只能命中一套。正式任务固定执行一次识别、筛选、美化、标签绑定和素材匹配；标签来自首次原图识别并最终归属于美化后的交付图片。任务创建时会冻结两套过滤标准和美化标准快照，后续编辑不会改变历史任务。

## 本地启动

```bash
docker compose -p image-intelligence up -d
docker compose -p image-intelligence exec -T api alembic upgrade head
cd frontend
npm run dev -- --port 5174 --strictPort
```

本地 API 地址为 `http://127.0.0.1:18000`，MinIO 预签名上传地址为 `http://127.0.0.1:19000`。前端通过 Vite 代理调用 API，不会将服务端 API Key 暴露给浏览器。

前端固定连接真实 API，不提供模拟数据分支。标准、标签树和素材元数据写入 PostgreSQL，素材文件写入 MinIO。

开发环境默认队列并发为：`control=1`、`preprocess=4`、`enhance=2`、`analysis=4`、`embedding=1`、`library=1`、`cleanup=1`；生产 Compose 也按队列拆分独立 Worker，避免 AI/向量任务阻塞调度、回调和清理。Redis 开启 AOF 并挂载独立持久卷，运行时 AI 配置不会随容器重建丢失。`celery-beat` 定期清理 30 天前的业务图片、24 小时未提交的上传对象，并重新投递超过 15 分钟没有进展的任务。素材库原图和向量不参与自动清理。

服务器部署时通过环境变量调整 Worker 数量和 `INFERENCE_DEVICE=auto`。CPU 服务器保持单个 embedding Worker；GPU 服务器可让 OpenCLIP 自动使用 CUDA。视觉 AI 默认全局限制为 24 次/分钟、4 并发，遇到 429、超时和 5xx 会自动退避。

所有 `/api/v1/*` 接口均需要 `X-API-Key` 请求头。请在本地 `.env` 或部署环境中设置高强度的 `API_KEY`，并仅由调用平台的服务端保存和发送该密钥。不要设置 `VITE_API_KEY` 或将服务密钥发送到浏览器。`/health` 提供存活检查；`/health/ready` 同时检查数据库连接、Alembic 版本、Redis、对象存储和关键配置，两者均不需要鉴权。

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
