# 图片智能处理台

面向装修图片分类、双路由过滤、美化与素材库匹配的前端工作台。

## 功能

- 多图选择与拖拽上传
- 本地缩略图预览
- 文件数量、格式和大小校验
- Presigned URL 上传流程
- 可配置的条件标准与美化标准
- 固定执行完工分类、后端双路由过滤、整批过滤完成、逐图美化、图片向量与内容特征混合匹配和素材库人工标签继承
- 扁平素材组、一组多标签、多张参考图和相似匹配
- 异步 Job 创建
- Job 进度轮询
- 保留、美化、淘汰与失败结果分组
- 原图 / 美化图查看
- 评分、原因、警告、淘汰码展示
- 多用户登录与管理员账号管理

## 启动

```bash
npm install
npm run dev
```

默认地址：

```text
http://127.0.0.1:5174
```

## API 连接

前端固定使用真实后端。Vite 开发服务器会将 `/api` 请求代理至 `http://127.0.0.1:18000`；浏览器通过 HttpOnly 会话 Cookie 登录，不读取或注入项目根目录的 `API_KEY`。不要在任何 `VITE_*` 变量中配置 API Key。

## 验证

```bash
npm run test
npm run build
npm audit --audit-level=moderate
```

## API

前端按开发方案对接以下接口：

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout
POST /api/v1/uploads/presign
POST /api/v1/image/jobs
GET  /api/v1/image/jobs/{job_id}
GET  /api/v1/image/jobs/{job_id}/results
POST /api/v1/uploads/presign-download
```
