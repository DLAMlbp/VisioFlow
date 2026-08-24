# Image Intelligence Service

多图智能筛选与自动美化服务。

当前实现为装修工匠照片处理 MVP。

## 已实现接口

```text
GET  /health
POST /api/v1/uploads/presign
POST /api/v1/uploads/presign-download
POST /api/v1/image/jobs
GET  /api/v1/image/jobs/{job_id}
GET  /api/v1/image/jobs/{job_id}/results
POST /api/v1/integration/jobs
GET  /api/v1/integration/jobs/{job_id}
GET  /api/v1/integration/jobs/{job_id}/results
```

## 第三方平台接入

第三方平台可使用以下接口一次上传图片并创建异步任务，无需自行对接 MinIO 预签名上传：

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

创建任务后的预处理 Worker 会读取原图，校验 Magic Bytes 与实际解码结果，提取尺寸、真实 MIME、文件大小、SHA256，并上传最长边 768px 的 JPEG 缩略图。元数据回写到 `image_items`，不新增对外 API。

Hard Filter 使用缩略图快速淘汰尺寸异常、极端模糊、极端过曝/欠曝与纯色图片。被淘汰的图片会记录 `reject_codes` 并提前结束，不进入后续质量检测或 AI 分析。

通过 Hard Filter 的图片会继续计算清晰度、曝光、对比度和噪声评分，全部归一化为 0-100，并写入 `image_metrics`。随后仅对保留图片执行自然增强，输出 JPEG 存储在 `enhanced/` 前缀下。

自然美化完成后，系统会向 AI 视觉模型发送最长边 1024px 的最终交付图副本，自动生成装修空间、施工阶段、拍摄视角、画面元素和风险提示标签。标签不参与自动淘汰；模型未配置、超时或识别失败时，图片仍保留，并在结果中返回标签失败状态。配置 `AI_TAGGING_API_KEY` 后，启动 `worker-tagging` 即可启用默认 OpenAI 视觉标签服务。

筛选与美化参数位于 `profiles/filters/renovation_submission_v1.yaml` 和 `profiles/beautify/renovation_natural_v1.yaml`，可直接编辑后重启 Worker 生效。

## 本地启动

```bash
docker compose -p image-intelligence up -d
docker compose -p image-intelligence exec -T api alembic upgrade head
cd frontend
npm run dev -- --port 5174 --strictPort
```

本地 API 地址为 `http://127.0.0.1:18000`，MinIO 预签名上传地址为 `http://127.0.0.1:19000`。前端通过 Vite 代理调用 API，不会将服务端 API Key 暴露给浏览器。

所有 `/api/v1/*` 接口均需要 `X-API-Key` 请求头。请在本地 `.env` 或部署环境中设置高强度的 `API_KEY`，并仅由调用平台的服务端保存和发送该密钥。不要设置 `VITE_API_KEY` 或将服务密钥发送到浏览器。`/health` 不需要鉴权，供容器和负载均衡健康检查使用。

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
