# Image Intelligence Service

公司服务端接入说明见 [`docs/company-api.md`](docs/company-api.md)。交付包中的所有 API Key 均为空，必须由部署方自行配置。

生产发布、健康验证、回滚和备份要求见 [`docs/production-runbook.md`](docs/production-runbook.md)。

面向装修图片的统一过滤标准、统一美化与素材库相似匹配服务。

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

正式模式固定执行“本地预检 → AI 在全部启用标准中唯一分类 → 只执行该标准的对应过滤规则 → 单图通过后立即美化 → 美化后并行生成 OpenCLIP 向量与大模型内容特征 → 素材库匹配 → 继承素材组人工标签”。分类、过滤、美化规划和内容特征识别使用已配置的 AI API Key；图片向量和最终标签不由大模型生成。新任务自动冻结全部启用标准，且必须配置恰好一条兜底分类；`processing_standards`、历史路由字段和 `max_selected` 只为旧客户端兼容，新任务会忽略。成功后立即返回 `201` 及 `job_id`，每张图片独立连续处理；进入终态后由 `control` Worker 主动向 `callback_url` 推送结果。

```bash
curl -X POST http://127.0.0.1:18000/api/v1/integration/jobs \
  -H "X-API-Key: <服务端 API Key>" \
  -F "files=@product-front.jpg;type=image/jpeg" \
  -F "files=@product-side.jpg;type=image/jpeg" \
  -F "beautify_profile=<标准管理中的美化标准 ID>" \
  -F "callback_url=https://client.example.com/api/image-callback"
```

任务进入 `completed`、`partial_failed`、`failed` 或 `cancelled` 后，服务会向 `callback_url` 发送 `POST application/json`。回调至少投递一次，接收方应按 `event_id` 或 `job_id + completed_at` 幂等处理，并校验 `X-Callback-Timestamp` 与 `X-Callback-Signature`；HTTP 2xx 表示接收成功。生产环境必须配置 `CALLBACK_ALLOWED_HOSTS` 和 `CALLBACK_SIGNING_SECRET`。`GET /api/v1/integration/jobs/{job_id}` 与 `/results` 保留为补偿和排障接口，不再要求客户端持续轮询。所有调用必须由对方平台的服务端发起，不能在浏览器或 App 中暴露 `X-API-Key`。

50 至 500 张的大批量任务使用 `POST /api/v1/upload-batches` 一次登记文件，再将文件并发直传对象存储，最后调用 `POST /api/v1/upload-batches/{batch_id}/complete`。任务内部每 25 张分片投递，用户仍只看到一个任务。结果接口支持 `limit`、`offset` 和 `decision`，默认每页 50 张。

生产 Web 网关对第三方集成接口按来源地址限制请求速率和并发连接；更大规模或多租户场景应在外部 API Gateway 中按调用方密钥配置独立配额。正式公网入口必须由负载均衡器或网关终止 HTTPS，Compose 暴露的 HTTP 端口不应直接对公网开放。

创建任务后的预处理 Worker 会读取原图，校验 Magic Bytes 与实际解码结果，提取尺寸、真实 MIME、文件大小、SHA256，并上传最长边 768px 的 JPEG 缩略图。元数据回写到 `image_items`，不新增对外 API。

本地预处理只拦截无法解码、超过系统资源上限或完全重复的文件，不再执行固定的业务过滤规则。

图片会计算清晰度、曝光、对比度和噪声评分，全部归一化为 0-100。视觉 AI 先只根据任务冻结的全部标准评估图片，并唯一命中一条标准；零条明确标准命中时进入唯一兜底分类。分类阶段不执行过滤。后端锁定命中的标准后，AI 仅执行该标准的“对应过滤规则”决定是否保留。单张图片过滤通过后立即按统一美化标准规划并执行，不等待同批其他图片。AI 调用或协议失败时只将当前图片标记失败并可重试，不会阻塞批次或回退到内置业务标准。输出 JPEG 存储在 `enhanced/` 前缀下。

分类与过滤请求优先使用严格 JSON Schema：分类阶段强制模型返回全部候选标准评估及唯一命中的 `standard_selection`，过滤阶段只接收命中标准的 `filter_rule`。兼容接口若以 HTTP 400/422 拒绝严格 Schema，系统会自动退回 `json_object`；返回 JSON 缺字段或字段类型不合法时，默认携带错误字段和脱敏后的上次输出纠错一次。最终仍不合法时图片明确失败，诊断写入 `image_items.ai_processing_diagnostic_json`，业务结果字段不会混入异常响应正文。纠错会产生一次额外 AI 调用，可通过 `AI_PROCESSING_SCHEMA_MAX_RETRIES` 调整；`AI_PROCESSING_MAX_COMPLETION_TOKENS` 控制响应上限，`AI_PROCESSING_STRICT_JSON_SCHEMA_ENABLED` 控制是否优先使用严格 Schema。

需要相似匹配时，系统会从美化后图片并行生成 OpenCLIP 向量和大模型结构化内容特征。OpenCLIP 先通过 pgvector 召回已启用素材组中的候选参考图，再按 70% 图片向量和 30% 内容特征计算综合分。图片分和综合分均达到 60% 时继承命中素材组的整套人工标签；内容特征缺失或候选不够明确时进入复核，低可信候选返回未匹配。大模型只描述场景、空间、状态、主体、对象、属性、OCR 和视角，不直接生成正式业务标签；匹配失败不影响图片保留和交付。

参考素材通过“素材库”页面单独上传。素材库使用扁平素材组：每组包含一串并列人工标签和多张参考图片，不存在父子层级。素材原图不会进入普通图片任务，也不会生成美化图；系统会进行格式校验、方向归一化、去重、OpenCLIP 向量生成和大模型内容特征识别。历史素材缺少内容特征时由后台任务自动补全。

素材库接口包括：

```text
GET   /api/v1/library/groups
POST  /api/v1/library/groups
PATCH /api/v1/library/groups/{group_id}
DELETE /api/v1/library/groups/{group_id}
POST  /api/v1/library/assets
GET   /api/v1/library/assets
PATCH /api/v1/library/assets/{asset_id}
POST  /api/v1/library/assets/{asset_id}/reindex
GET   /api/v1/tag-reviews
POST  /api/v1/tag-reviews/{image_id}/decision
```

当前匹配权重和阈值位于 `profiles/tags/library_similarity_v2.yaml`；`v1` 保留为初始基线。后续校准应继续新增配置版本，不直接覆盖历史版本。

阈值上线前应准备人工标注的 CSV 或 JSON 候选对，字段为 `query_id`、`expected_asset_id`、`candidate_asset_id`、`similarity_score`、`feature_score` 和 `final_score`；后两项允许省略以兼容历史纯向量样本。再运行：

```bash
python scripts/calibrate_library_matching.py labels.csv --target-precision 0.97 --target-review-recall 0.95 --min-auto-matches 30 --output calibration-report.json
```

脚本输出每组 Top-1、Top-2、Top-5 和 margin，并且只会在至少 300 组正样本、300 组负样本确实达到目标精确率与复核召回率时给出 `auto_threshold`、`review_threshold` 和 `min_margin` 建议；样本不足或达不到目标时返回 `insufficient_evidence`，不会把测试目标当成生产准确率。

标准管理只维护“过滤标准”和“美化标准”。每条过滤标准包含标准名称、分类标准、对应过滤规则和兜底标记，分类与过滤一一对应；启用中的标准必须且只能有一条兜底分类。任务自动冻结全部启用标准，逐图选择唯一标准，过滤通过后立即启动统一美化。美化后图片通过 OpenCLIP 图片向量和大模型内容特征混合匹配素材库；自动匹配成功后只继承素材组人工标签，待复核和未匹配结果的正式标签为空。后续编辑不会改变历史任务。

## 本地启动

```bash
docker compose -p image-intelligence up -d
docker compose -p image-intelligence exec -T api alembic upgrade head
cd frontend
npm run dev -- --port 5174 --strictPort
```

本地 API 地址为 `http://127.0.0.1:18000`，MinIO 预签名上传地址为 `http://127.0.0.1:19000`。前端通过 Vite 代理调用 API，不会将服务端 API Key 暴露给浏览器。

前端固定连接真实 API，不提供模拟数据分支。标准、素材组标签和素材元数据写入 PostgreSQL，素材文件写入 MinIO。

开发环境默认队列并发为：`control=1`、`preprocess=4`、`enhance=2`、`analysis=4`、`embedding=1`、`library=1`、`cleanup=1`；生产 Compose 也按队列拆分独立 Worker，避免 AI/向量任务阻塞调度、回调和清理。Redis 开启 AOF 并挂载独立持久卷，运行时 AI 配置不会随容器重建丢失。`celery-beat` 定期清理 30 天前的业务图片、24 小时未提交的上传对象，并重新投递超过 15 分钟没有进展的任务。素材库原图和向量不参与自动清理。

服务器部署时通过环境变量调整 Worker 数量和 `INFERENCE_DEVICE=auto`。CPU 服务器保持单个 embedding Worker；GPU 服务器可让 OpenCLIP 自动使用 CUDA。视觉 AI 默认全局限制为 24 次/分钟、4 并发，遇到 429、超时和 5xx 会自动退避。

所有 `/api/v1/*` 接口均需要 `X-API-Key` 请求头。请在本地 `.env` 或部署环境中设置高强度的 `API_KEY`，并仅由调用平台的服务端保存和发送该密钥。不要设置 `VITE_API_KEY` 或将服务密钥发送到浏览器。`/health` 提供存活检查；`/health/ready` 同时检查数据库连接、Alembic 版本、Redis、对象存储和关键配置，两者均不需要鉴权。

`COMPLETION_ROUTING_ENABLED`、`BATCH_FILTER_BARRIER_ENABLED`、`POST_FILTER_BEAUTIFY_PLAN_ENABLED`、`LIBRARY_IMAGE_ONLY_MATCHING_ENABLED`、`LIBRARY_ONLY_TAGS_ENABLED` 是强制工作流开关，正式运行必须全部为 `true`。`LIBRARY_IMAGE_ONLY_MATCHING_ENABLED` 是历史环境变量名，现在控制混合匹配主链路。任一开关关闭后，新任务会被拒绝，正在等待对应阶段的任务会暂停，系统不会回退旧流水线。`LIBRARY_MATCH_SHADOW_MODE=true` 只把自动匹配降为待人工复核，不改变素材组人工标签约束。

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
