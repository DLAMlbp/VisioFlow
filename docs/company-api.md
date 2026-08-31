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
Content-Type: application/json
X-API-Key: <INTEGRATION_API_KEY>
```

创建请求必须包含 `notifyUrl` 和 `images[].objectKey/imageUrl`。图片服务下载 URL 图片并保存客户 `objectKey`，任务进入终态后由 `control` Worker 按客户协议回调完整结果。

正式模式固定执行“下载 URL 图片 -> AI 完工分类 -> 完工/施工分支过滤 -> 整批过滤完成 -> AI 规划并本地美化 -> OpenCLIP 图片向量与大模型内容特征混合匹配 -> 继承素材组人工标签”。客户不需要传内部处理标准，服务端使用已配置的正式标准。

```bash
curl -X POST "https://<service-host>/api/v1/integration/jobs" \
  -H "X-API-Key: $INTEGRATION_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"notifyUrl":"https://client.example.com/api/image-callback","images":[{"objectKey":"customer/image-1.jpg","imageUrl":"https://obs.example.com/image-1.jpg"}]}'
```

支持 JPEG、PNG、WebP；单张不超过 25 MB；单次最多 50 张。成功返回 HTTP 201：

```json
{"job_id":"job_xxx","status":"queued","total":1,"ok":true,"code":"","message":"","taskId":"job_xxx"}
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

回调体包含客户原始 `objectKey`、判定、0～100 质量分、美化图临时地址和标签。签名原文为 UTF-8 字节串 `<timestamp>.<raw_request_body>`，使用双方共享密钥计算 HMAC-SHA256。接收方返回任意 HTTP 2xx 即视为成功；非 2xx、连接失败或超时会指数退避重试，默认最多 5 次。接收方应按 `X-Callback-Id` 幂等处理。

```json
{
  "results": [
    {
      "objectKey": "customer/image-1.jpg",
      "decision": "selected",
      "score": 86.5,
      "enhancedUrl": "https://storage.example.com/signed-result",
      "enhancedMd5": null,
      "aiTags": ["客厅", "采光好"]
    }
  ],
  "errorMessage": ""
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

结果包含 `pipeline_stage`、分类/过滤/美化/内容分析/向量/匹配子状态、命中的过滤标准、实际过滤结论、质量分、美化说明和素材库匹配标签，以及 `original_url`、`enhanced_url` 两个限时下载地址。正式标签只读取 `library_tags.tags`；待复核或未匹配时该数组为空。下载地址有效期见响应中的 `download_expires_in`，过期后重新请求结果即可获得新地址。

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
