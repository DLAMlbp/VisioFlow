# 装修照片处理台

装修工匠批量照片筛选与自然美化服务的前端工作台。

## 功能

- 多图选择与拖拽上传
- 本地缩略图预览
- 文件数量、格式和大小校验
- Presigned URL 上传流程
- 装修照片基础筛选与自然美化 Profile
- 异步 Job 创建
- Job 进度轮询
- 保留、美化、淘汰与失败结果分组
- 原图 / 美化图查看
- 评分、原因、警告、淘汰码展示
- Mock API 模式，可在后端未完成时先体验完整流程

## 启动

```bash
npm install
npm run dev
```

默认地址：

```text
http://127.0.0.1:5174
```

## 环境变量

当前本地环境已经配置为真实后端模式：

```env
VITE_USE_MOCK_API=false
```

Vite 开发服务器会将 `/api` 请求代理至 `http://127.0.0.1:18000`，并在服务器端读取项目根目录 `.env` 的 `API_KEY`。不要在任何 `VITE_*` 变量中配置 API Key。

## 验证

```bash
npm run test
npm run build
npm audit --audit-level=moderate
```

## API

前端按开发方案对接以下接口：

```text
POST /api/v1/uploads/presign
POST /api/v1/image/jobs
GET  /api/v1/image/jobs/{job_id}
GET  /api/v1/image/jobs/{job_id}/results
POST /api/v1/uploads/presign-download
```
