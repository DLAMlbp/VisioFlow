# 公司系统接入 API

服务采用异步任务模式，适合图片处理通常需要数秒到数十秒的场景。公司服务端按“创建任务 → 查询进度 → 获取结果”调用。

## 配置

交付包不包含任何密钥。部署方必须自行配置：

```dotenv
API_KEY=
INTEGRATION_API_KEY=
AI_TAGGING_API_KEY=
```

- `API_KEY`：内部管理接口密钥，不提供给业务调用方。
- `INTEGRATION_API_KEY`：公司业务服务调用本接口时使用。
- `AI_TAGGING_API_KEY`：AI 中转站密钥，也可在管理前端中配置。

三个密钥不得复用，不得写入浏览器、App 或公开代码仓库。外部调用必须使用 HTTPS；当前 HTTP 地址只适合受控网络内联调。

## 1. 创建处理任务

```http
POST /api/v1/integration/jobs
Content-Type: multipart/form-data
X-API-Key: <INTEGRATION_API_KEY>
```

```bash
curl -X POST "https://<service-host>/api/v1/integration/jobs" \
  -H "X-API-Key: $INTEGRATION_API_KEY" \
  -F "files=@living-room.jpg;type=image/jpeg" \
  -F "files=@bedroom.png;type=image/png" \
  -F "max_selected=10"
```

支持 JPEG、PNG、WebP；单张不超过 25 MB；单次最多 50 张；整个 HTTP 请求不超过 512 MB。成功返回 HTTP 201：

```json
{"job_id":"job_xxx","status":"queued","total":2}
```

## 2. 查询任务进度

```bash
curl "https://<service-host>/api/v1/integration/jobs/job_xxx" \
  -H "X-API-Key: $INTEGRATION_API_KEY"
```

当 `status` 为 `completed`、`partial_failed`、`failed` 或 `cancelled` 时停止轮询。建议每 2 秒查询一次。

## 3. 获取结果

```bash
curl "https://<service-host>/api/v1/integration/jobs/job_xxx/results?limit=50&offset=0" \
  -H "X-API-Key: $INTEGRATION_API_KEY"
```

结果包含筛选决定、质量分、美化说明、AI 标签、相似素材匹配，以及 `original_url`、`enhanced_url` 两个限时下载地址。下载地址有效期见响应中的 `download_expires_in`，过期后重新请求结果即可获得新地址。

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
