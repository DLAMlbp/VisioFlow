# 公司系统接入 API

服务采用异步任务模式，适合图片处理通常需要数秒到数十秒的场景。公司服务端按“创建任务 → Worker 异步处理 → 结果回调”调用；查询接口仅用于补偿和排障。

## 配置

交付包不包含任何密钥。部署方必须自行配置：

```dotenv
API_KEY=
INTEGRATION_API_KEY=
AI_TAGGING_API_KEY=
AI_CONFIG_ENCRYPTION_KEY=
CALLBACK_SIGNING_SECRET=
CALLBACK_ALLOWED_HOSTS=client.example.com
```

- `API_KEY`：内部管理接口密钥，不提供给业务调用方。
- `INTEGRATION_API_KEY`：公司业务服务调用本接口时使用。
- `AI_TAGGING_API_KEY`：AI 中转站密钥，也可在管理前端中配置。

- `AI_CONFIG_ENCRYPTION_KEY`：用于加密保存在 Redis 中的运行时 AI 密钥。
- `CALLBACK_SIGNING_SECRET`：回调 HMAC-SHA256 签名密钥，至少 32 个字符。
- `CALLBACK_ALLOWED_HOSTS`：允许接收回调的主机名白名单，多个值以逗号分隔。

三个密钥不得复用，不得写入浏览器、App 或公开代码仓库。外部调用必须使用 HTTPS；当前 HTTP 地址只适合受控网络内联调。

## 1. 创建处理任务

```http
POST /api/v1/integration/jobs
Content-Type: multipart/form-data
X-API-Key: <INTEGRATION_API_KEY>
```

创建请求必须包含 `callback_url`。该地址由调用方服务端提供，任务进入终态后由图片服务的 `control` Worker 主动 `POST` 完整结果。

正式模式固定执行“AI 完工分类 -> 完工/施工分支过滤 -> 整批过滤完成 -> AI 规划并本地美化 -> OpenCLIP 图片向量与大模型内容特征混合匹配 -> 继承素材组人工标签”。大模型内容特征只用于候选比对，不会直接生成正式标签。`completion_profile`、`completed_filter_profile`、`non_completed_filter_profile` 和 `beautify_profile` 均为必填。`processing_standards` 仅作为旧请求字段保留，不再允许用于创建新任务。历史阶段开关字段仍保留在 v1，但传 `false` 会返回参数错误。

```bash
curl -X POST "https://<service-host>/api/v1/integration/jobs" \
  -H "X-API-Key: $INTEGRATION_API_KEY" \
  -F "files=@product-front.jpg;type=image/jpeg" \
  -F "files=@product-side.png;type=image/png" \
  -F "completion_profile=<完工分类标准 ID>" \
  -F "completed_filter_profile=<完工过滤标准 ID>" \
  -F "non_completed_filter_profile=<非完工过滤标准 ID>" \
  -F "beautify_profile=<标准管理中的美化标准 ID>" \
  -F "callback_url=https://client.example.com/api/image-callback" \
  -F "max_selected=10"
```

支持 JPEG、PNG、WebP；单张不超过 25 MB；单次最多 50 张；整个 HTTP 请求不超过 512 MB。成功返回 HTTP 201：

```json
{"job_id":"job_xxx","status":"queued","total":2}
```

## 2. 接收结果回调

任务进入 `completed`、`partial_failed`、`failed` 或 `cancelled` 后，服务执行：

```http
POST {callback_url}
Content-Type: application/json
X-Callback-Event: image.job.finished
X-Callback-Id: <event_id>
X-Callback-Timestamp: <Unix 秒时间戳>
X-Callback-Signature: sha256=<HMAC 十六进制摘要>
```

回调体包含 `event_id`、`job_id`、`status`、`completed_at`、结果计数、限时下载地址和逐图结果。签名原文为 UTF-8 字节串 `<timestamp>.<raw_request_body>`，使用双方共享密钥计算 HMAC-SHA256；接收方应使用常量时间比较，并拒绝时间戳偏差过大的请求。接收方返回任意 HTTP 2xx 即视为成功。非 2xx、连接失败或超时会指数退避重试，默认最多 5 次。回调采用至少一次投递语义，接收方必须按 `event_id` 幂等处理。

```json
{
  "event_id": "job_xxx:2026-08-29T08:30:00+00:00",
  "event": "image.job.finished",
  "job_id": "job_xxx",
  "status": "completed",
  "completed_at": "2026-08-29T08:30:00Z",
  "total": 2,
  "selected": 1,
  "rejected": 1,
  "not_selected": 0,
  "result_total": 2,
  "download_expires_in": 900,
  "images": [],
  "error_message": null
}
```

## 3. 补偿查询任务进度

```bash
curl "https://<service-host>/api/v1/integration/jobs/job_xxx" \
  -H "X-API-Key: $INTEGRATION_API_KEY"
```

该接口保留用于回调延迟时排障，不再要求业务方持续轮询。

## 4. 补偿获取结果

```bash
curl "https://<service-host>/api/v1/integration/jobs/job_xxx/results?limit=50&offset=0" \
  -H "X-API-Key: $INTEGRATION_API_KEY"
```

结果包含完工分类、实际过滤分支、质量分、美化说明和素材库匹配标签，以及 `original_url`、`enhanced_url` 两个限时下载地址。正式标签只读取 `library_tags.tags`；待复核或未匹配时该数组为空。下载地址有效期见响应中的 `download_expires_in`，过期后重新请求结果即可获得新地址。

## 状态码

- `201`：任务创建成功。
- `400`：文件格式、大小、数量或参数不合法。
- `401`：集成密钥缺失或错误。
- `404`：任务不存在。
- `413`：整个上传请求超过 512 MB。
- `503`：部署方尚未配置 `INTEGRATION_API_KEY`，或依赖服务未就绪。

健康检查无需密钥：`GET /health`、`GET /health/ready`。

## API 稳定性约定

`/api/v1/integration/*` 是长期稳定接口。后续服务功能更新必须保持现有路径、HTTP 方法、鉴权头、字段名称、字段类型、状态和错误语义向后兼容。允许新增可选字段；客户应忽略不认识的响应字段。任何破坏性变化必须发布新的 `/api/v2`，不得直接修改 v1 导致现有客户调用失败。每次发布必须通过接口契约测试、健康检查和端到端回归。
