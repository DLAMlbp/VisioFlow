# Image Intelligence Service

本次 Harbor 交付（后端 `visioflow:v20260905`）请先按 [Harbor 首次部署说明](docs/harbor-first-deployment.md) 发布独立前端镜像并配置环境。使用 `bash scripts/customer-compose.sh logs --tail 100` 可自动加载 `.env.production`。以下 GHCR 示例适用于原发布方式。

公司服务端接入说明见 [`docs/company-api.md`](docs/company-api.md)。交付包中的所有 API Key 均为空，必须由部署方自行配置。

生产发布、健康验证、回滚和备份要求见 [`docs/production-runbook.md`](docs/production-runbook.md)。

面向装修图片的统一过滤标准、统一美化与素材库相似匹配服务。

## 客户生产部署（Docker Compose）

> 完整说明见 [`docs/客户服务器部署与更新操作手册.md`](docs/客户服务器部署与更新操作手册.md)，生产发布、备份与恢复规则见 [`docs/production-runbook.md`](docs/production-runbook.md)。

生产服务器只负责拉取版本化镜像、运行容器、健康检查和回滚，**不要在服务器执行 `docker build`、`npm run build` 或其他源码构建命令，也不要使用 `latest` 镜像**。当前 GitLab 项目保存源码和部署配置；`docker-compose.prod.yml` 默认从 GHCR 拉取已由 CI 构建的生产镜像。

### 1. 部署前准备

- 推荐 Ubuntu Server 22.04/24.04、x86_64、Docker Engine 和 Docker Compose v2。
- 推荐 12 核 CPU、24–32 GiB 内存和至少 100 GiB SSD；低于 16 GiB 内存必须先压测。
- 准备 GitLab 只读代码权限；私有 GHCR 镜像还需准备仅有 `read:packages` 权限的读取令牌。
- 准备 Web/API 域名、文件访问域名、AI 服务密钥和客户回调域名。

在运维工作站取得待发布版本。`FULL_GIT_SHA` 必须替换成已经通过 CI 的完整 40 位提交 SHA：

```bash
git clone git@code.fengjiangit.com:aicode/visioflow.git
cd visioflow
git fetch origin --tags
git checkout --detach FULL_GIT_SHA
git rev-parse HEAD
```

把生产 Compose 和环境变量模板复制到服务器：

```bash
scp docker-compose.prod.yml .env.production.example \
  ADMIN_USER@CUSTOMER_SERVER:/tmp/
```

### 2. 首次部署

登录服务器并安装部署文件：

```bash
ssh ADMIN_USER@CUSTOMER_SERVER
sudo -i
umask 077
install -d -o root -g root -m 750 /opt/image-intelligence
install -o root -g root -m 640 \
  /tmp/docker-compose.prod.yml \
  /opt/image-intelligence/docker-compose.prod.yml
install -o root -g root -m 600 \
  /tmp/.env.production.example \
  /opt/image-intelligence/.env.production
cd /opt/image-intelligence
```

为每个密码或密钥分别生成随机值，并立即保存到客户的密码管理系统：

```bash
openssl rand -hex 32
```

编辑生产配置：

```bash
sudoedit /opt/image-intelligence/.env.production
```

至少替换模板中的域名、IP、数据库/MinIO 密码以及以下密钥，不能保留空值或示例值：

```dotenv
API_KEY=独立随机值
INTEGRATION_API_KEY=独立随机值
AUTH_SESSION_SECRET=独立随机值
AI_TAGGING_API_KEY=AI服务密钥
AI_CONFIG_ENCRYPTION_KEY=独立随机值
CALLBACK_SIGNING_SECRET=独立随机值
POSTGRES_PASSWORD=独立随机值
MINIO_ROOT_PASSWORD=独立随机值
S3_SECRET_KEY=与MINIO_ROOT_PASSWORD一致
```

所有应用组件必须指向同一个已经通过 CI 的版本；把 `FULL_GIT_SHA` 替换为真实完整 SHA：

```dotenv
API_IMAGE=ghcr.io/zuixi01/tuxiangshibie-api:FULL_GIT_SHA
API_GATEWAY_IMAGE=ghcr.io/zuixi01/tuxiangshibie-api:FULL_GIT_SHA
CLASSIFICATION_IMAGE=ghcr.io/zuixi01/tuxiangshibie-api:FULL_GIT_SHA
RENDER_IMAGE=ghcr.io/zuixi01/tuxiangshibie-api:FULL_GIT_SHA
WEB_IMAGE=ghcr.io/zuixi01/tuxiangshibie-web:FULL_GIT_SHA
```

以下生产工作流开关必须全部为 `true`：

```dotenv
COMPLETION_ROUTING_ENABLED=true
BATCH_FILTER_BARRIER_ENABLED=true
POST_FILTER_BEAUTIFY_PLAN_ENABLED=true
COMBINED_CLASSIFY_FILTER_ENABLED=true
EARLY_SEMANTIC_BRANCH_ENABLED=true
LIBRARY_IMAGE_ONLY_MATCHING_ENABLED=true
LIBRARY_ONLY_TAGS_ENABLED=true
```

如果 GHCR 镜像为私有包，使用只读令牌登录；令牌不要写进 README、`.env.production` 或命令历史：

```bash
read -s GHCR_TOKEN
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io \
  -u GITHUB_USER --password-stdin
unset GHCR_TOKEN
```

### 3. 发布前安全检查

每次首次部署、更新或回滚前都执行：

```bash
cd /opt/image-intelligence
uptime
free -h
swapon --show
df -h
df -ih
docker system df
docker compose --env-file .env.production \
  -f docker-compose.prod.yml ps --all
```

出现下列任一情况时停止发布并先排障：磁盘或 inode 使用率达到 85%；可用磁盘不足 10 GiB 或文件系统容量的 20%；可用内存不足 2 GiB 或总内存的 25%；最近 5 分钟负载高于 CPU 核心数；已有构建、备份、迁移任务运行；系统出现 OOM 或磁盘错误。不要直接执行 `docker system prune`。

### 4. 启动与验证

先校验配置，再拉取和启动指定版本镜像：

```bash
cd /opt/image-intelligence
docker compose --env-file .env.production \
  -f docker-compose.prod.yml config --quiet
docker compose --env-file .env.production \
  -f docker-compose.prod.yml pull
docker compose --env-file .env.production \
  -f docker-compose.prod.yml up -d --remove-orphans
docker compose --env-file .env.production \
  -f docker-compose.prod.yml ps --all
```

验证服务和实际运行镜像：

```bash
curl --fail --silent http://127.0.0.1:8088/health/ready
docker compose --env-file .env.production \
  -f docker-compose.prod.yml images
docker compose --env-file .env.production \
  -f docker-compose.prod.yml logs --since 10m --tail 200 \
  api worker-control worker-callback
```

首次启动且数据库迁移完成后，使用服务端 `API_KEY` 创建第一个管理员。该接口仅在系统尚无管理员时允许执行，密码至少 10 位：

```bash
curl --fail --request POST http://127.0.0.1:8088/api/v1/auth/bootstrap \
  --header "X-API-Key: 替换为服务端API_KEY" \
  --header "Content-Type: application/json" \
  --data '{"username":"admin","display_name":"系统管理员","password":"替换为高强度初始密码","role":"admin"}'
```

用户也可以在登录页使用用户名或邮箱自助注册，注册成功后会以“操作员”角色直接登录，不能自行取得管理员权限。注册按来源 IP 限制为默认每分钟 5 次，可通过 `AUTH_REGISTRATION_RATE_LIMIT_PER_MINUTE` 调整。管理员仍可在“账号管理”中创建、停用和调整其他账号。

`/health/ready` 必须返回 `ready`，其中数据库迁移、Redis、对象存储和关键配置均应为 `ok`。随后用少量测试图片完成一次端到端任务，不要直接放入生产全量流量。

### 5. 更新到新版本

先在运维工作站检出新的、已经通过 CI 的提交，并重新上传生产 Compose：

```bash
git fetch origin --tags
git checkout --detach NEW_FULL_GIT_SHA
git rev-parse HEAD
scp docker-compose.prod.yml \
  ADMIN_USER@CUSTOMER_SERVER:/tmp/docker-compose.prod.yml
```

服务器先保存当前可回滚配置，再安装新版 Compose：

```bash
sudo -i
cd /opt/image-intelligence
DEPLOY_ID="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o root -g root -m 700 ".deployments/$DEPLOY_ID"
cp --preserve=mode,ownership .env.production \
  ".deployments/$DEPLOY_ID/.env.production"
cp --preserve=mode,ownership docker-compose.prod.yml \
  ".deployments/$DEPLOY_ID/docker-compose.prod.yml"
install -o root -g root -m 640 \
  /tmp/docker-compose.prod.yml docker-compose.prod.yml
echo "回滚快照：$DEPLOY_ID"
```

编辑 `.env.production`，把 `API_IMAGE`、`API_GATEWAY_IMAGE`、`CLASSIFICATION_IMAGE`、`RENDER_IMAGE` 和 `WEB_IMAGE` 全部更新为同一个 `NEW_FULL_GIT_SHA`，然后依次执行“发布前安全检查”和“启动与验证”中的命令。

### 6. 回滚

如果健康检查或端到端验证失败，停止继续发布，不在服务器修改代码或重新构建。使用上一步输出的快照编号回滚：

```bash
sudo -i
cd /opt/image-intelligence
PREVIOUS_DEPLOY_ID=替换为回滚快照编号
cp --preserve=mode,ownership \
  ".deployments/$PREVIOUS_DEPLOY_ID/.env.production" .env.production
cp --preserve=mode,ownership \
  ".deployments/$PREVIOUS_DEPLOY_ID/docker-compose.prod.yml" \
  docker-compose.prod.yml
docker compose --env-file .env.production \
  -f docker-compose.prod.yml pull
docker compose --env-file .env.production \
  -f docker-compose.prod.yml up -d --remove-orphans
docker compose --env-file .env.production \
  -f docker-compose.prod.yml ps --all
curl --fail --silent http://127.0.0.1:8088/health/ready
```

数据库迁移默认只允许向前兼容。不要在自动回滚中运行 `alembic downgrade`；确需数据库降级时必须先评估数据损失并单独审批。

## 已实现接口

```text
GET  /health
GET  /health/ready
POST /api/v1/auth/login
POST /api/v1/auth/register
POST /api/v1/auth/bootstrap
GET  /api/v1/auth/me
POST /api/v1/auth/logout
GET  /api/v1/auth/users
POST /api/v1/auth/users
PATCH /api/v1/auth/users/{user_id}
POST /api/v1/auth/users/{user_id}/reset-password
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

第三方平台使用公网图片 URL 创建小批量任务：

```text
POST /api/v1/integration/jobs
Content-Type: application/json
X-API-Key: <服务端 API Key>
```

正式模式固定执行“下载 URL 图片 → 本地预检 → 一次视觉 AI 完成分类与对应标准过滤 → 过滤通过后并行执行美化、内容分析和 OpenCLIP 向量 → 素材库匹配与增强结果汇合 → 继承素材组人工标签”。服务端使用已配置的正式标准，客户只需提供 `notifyUrl` 以及每张图片的 `objectKey`、`imageUrl`。成功后立即返回 `201`、`job_id` 和兼容字段 `taskId`；进入终态后由 `control` Worker按客户协议回调 `results`。

```bash
curl -X POST http://127.0.0.1:18000/api/v1/integration/jobs \
  -H "X-API-Key: <服务端 API Key>" \
  -H "Content-Type: application/json" \
  -d '{"notifyUrl":"https://client.example.com/api/image-callback","images":[{"objectKey":"customer/image-1.jpg","imageUrl":"https://obs.example.com/image-1.jpg"}]}'
```

任务进入 `completed`、`partial_failed`、`failed` 或 `cancelled` 后，服务会向 `notifyUrl` 发送 `POST application/json`，回调体为 `results[].objectKey/decision/score/enhancedUrl/enhancedMd5/aiTags`。回调至少投递一次，接收方应按 `X-Callback-Id` 幂等处理，并可校验 `X-Callback-Timestamp` 与 `X-Callback-Signature`；HTTP 2xx 表示接收成功。`GET /api/v1/integration/jobs/{job_id}` 与 `/results` 保留为补偿和排障接口。原 multipart 请求仍可通过同一路径或 `/api/v1/integration/file-jobs` 使用。

50 至 500 张的大批量任务使用 `POST /api/v1/upload-batches` 一次登记文件，再将文件并发直传对象存储，最后调用 `POST /api/v1/upload-batches/{batch_id}/complete`。任务内部每 25 张分片投递，用户仍只看到一个任务。结果接口支持 `limit`、`offset` 和 `decision`，默认每页 50 张。

生产 Web 网关对第三方集成接口按来源地址限制请求速率和并发连接；更大规模或多租户场景应在外部 API Gateway 中按调用方密钥配置独立配额。正式公网入口必须由负载均衡器或网关终止 HTTPS，Compose 暴露的 HTTP 端口不应直接对公网开放。

创建任务后的预处理 Worker 会读取原图，校验 Magic Bytes 与实际解码结果，提取尺寸、真实 MIME、文件大小、SHA256，在方向校正后将内部处理副本居中裁剪为宽高比严格等于 3:4 的竖图，并上传最长边 768px 的 JPEG 缩略图。原始上传对象保持不变；分类过滤、去水印、美化、内容分析、向量和最终交付统一使用同一构图。裁剪范围、处理前后尺寸和保留面积比例记录在 `image_items.normalization_json`，不新增对外 API。

本地预处理只拦截无法解码、超过系统资源上限、无法形成有效 3:4 画面或完全重复的文件，不再执行固定的业务过滤规则。过滤标准描述中的图片比例统一写作“3:4 竖图（宽:高）”，不得写成横向 4:3，也不得因上传原图的比例不同而淘汰图片。

图片会基于 3:4 处理图计算清晰度、曝光、对比度和噪声评分，全部归一化为 0-100。一次视觉 AI 请求会根据任务冻结的全部标准唯一命中一条标准，并只执行该标准的对应过滤规则；零条明确标准命中时进入唯一兜底分类。分类评估和过滤决定分别保存并在同一事务内落库，避免半完成状态。单张图片过滤通过后立即并行启动美化规划、内容分析和 OpenCLIP 向量，不等待同批其他图片。AI 调用或协议失败时只将当前图片标记失败并可重试，不会阻塞批次或回退到内置业务标准。输出 JPEG 存储在 `enhanced/` 前缀下；新流程图片在写入交付对象前再次校验宽高比必须严格等于 3:4。

分类与过滤合并请求使用单次严格 JSON Schema：模型必须返回全部候选标准评估、唯一命中的 `standard_selection`，以及仅针对命中标准的 `filter`。兼容接口拒绝严格 Schema、返回 JSON 缺字段或字段类型不合法时，任务立即在当前节点失败，不再自动回退或纠错重试；脱敏诊断写入 `image_items.ai_processing_diagnostic_json`，失败节点同步返回进度、结果和客户回调。`AI_PROCESSING_MAX_COMPLETION_TOKENS` 控制响应上限，`AI_PROCESSING_STRICT_JSON_SCHEMA_ENABLED` 控制是否使用严格 Schema。

需要相似匹配时，系统会从预处理阶段生成的方向归一化 JPEG 并行生成预向量和大模型结构化内容特征，与美化分支同时执行。预向量只用于提前加载模型和验证图片可编码，不能触发最终素材匹配；美化完成后必须从最终交付图刷新权威 OpenCLIP 向量，再通过 pgvector 召回已启用素材组中的候选参考图，并按 70% 图片向量和 30% 内容特征计算综合分。图片分和综合分均达到 60% 时继承命中素材组的整套人工标签；内容特征缺失或候选不够明确时进入复核，低可信候选返回未匹配。大模型只描述场景、空间、状态、主体、对象、属性、OCR 和视角，不直接生成正式业务标签；只有增强和基于交付图的匹配都完成后图片才会进入交付终态。

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
```

当前匹配权重和阈值位于 `profiles/tags/library_similarity_v2.yaml`；`v1` 保留为初始基线。后续校准应继续新增配置版本，不直接覆盖历史版本。

阈值上线前应准备人工标注的 CSV 或 JSON 候选对，字段为 `query_id`、`expected_asset_id`、`candidate_asset_id`、`similarity_score`、`feature_score` 和 `final_score`；后两项允许省略以兼容历史纯向量样本。再运行：

```bash
python scripts/calibrate_library_matching.py labels.csv --target-precision 0.97 --target-review-recall 0.95 --min-auto-matches 30 --output calibration-report.json
```

脚本输出每组 Top-1、Top-2、Top-5 和 margin，并且只会在至少 300 组正样本、300 组负样本确实达到目标精确率与复核召回率时给出 `auto_threshold`、`review_threshold` 和 `min_margin` 建议；样本不足或达不到目标时返回 `insufficient_evidence`，不会把测试目标当成生产准确率。

标准管理只维护“过滤标准”和“美化标准”。每条过滤标准包含标准名称、分类标准、对应过滤规则和兜底标记，分类与过滤一一对应；启用中的标准必须且只能有一条兜底分类。任务自动冻结全部启用标准，一次请求逐图选择唯一标准并完成过滤，过滤通过后并行启动统一美化和语义匹配。自动匹配成功后只继承素材组人工标签，待复核和未匹配结果的正式标签为空。后续编辑不会改变历史任务。

## 本地启动

```bash
docker compose -p image-intelligence up -d
docker compose -p image-intelligence exec -T api alembic upgrade head
cd frontend
npm run dev -- --port 5174 --strictPort
```

本地 API 地址为 `http://127.0.0.1:18000`，MinIO 预签名上传地址为 `http://127.0.0.1:19000`。前端通过 Vite 代理调用 API，不会将服务端 API Key 暴露给浏览器。

前端固定连接真实 API，不提供模拟数据分支。标准、素材组标签和素材元数据写入 PostgreSQL，素材文件写入 MinIO。

开发环境默认队列并发为：`control=1`、`preprocess=4`、`classification=4`、`beautify_plan=4`、`enhance=1`、`analysis=1`、`openclip=1`、`matching=2`、`cleanup=1`。在线向量和图库向量由同一个 `openclip` Worker 处理，只保留一份常驻模型；在线任务优先级高于图库补全。生产 Compose 也按角色加载所需任务模块，避免无关Worker加载完整任务和图像依赖。Redis 开启 AOF 并挂载独立持久卷，运行时 AI 配置不会随容器重建丢失。`celery-beat` 定期清理 30 天前的业务图片、24 小时未提交的上传对象，并重新投递超过 15 分钟没有进展的任务。素材库原图和向量不参与自动清理。

服务器部署时通过环境变量调整 Worker 数量和 `INFERENCE_DEVICE=auto`。CPU 服务器保持单个 `openclip` Worker 且并发为 1；GPU 服务器可让 OpenCLIP 自动使用 CUDA。视觉 AI 默认全局限制为 24 次/分钟、4 并发，遇到 429、超时和 5xx 会自动退避。`COMBINED_CLASSIFY_FILTER_ENABLED` 和 `EARLY_SEMANTIC_BRANCH_ENABLED` 默认开启，紧急回滚时可分别恢复旧的两次AI调用和增强后语义链路。

Web 工作台使用 HttpOnly 会话 Cookie 登录，写操作同时校验 CSRF；管理员负责创建、停用账号、切换角色和重置密码。服务端调用仍可对业务接口发送 `X-API-Key`，但该密钥具有管理员级权限，只能由受信任的服务端保存和发送，不能配置为 `VITE_API_KEY` 或暴露给浏览器。第三方集成接口继续单独使用 `INTEGRATION_API_KEY`。`/health` 提供存活检查；`/health/ready` 同时检查数据库连接、Alembic 版本、Redis、对象存储和关键配置，两者均不需要鉴权。

`COMPLETION_ROUTING_ENABLED`、`BATCH_FILTER_BARRIER_ENABLED`、`POST_FILTER_BEAUTIFY_PLAN_ENABLED`、`LIBRARY_IMAGE_ONLY_MATCHING_ENABLED`、`LIBRARY_ONLY_TAGS_ENABLED` 是强制工作流开关，正式运行必须全部为 `true`。`LIBRARY_IMAGE_ONLY_MATCHING_ENABLED` 是历史环境变量名，现在控制混合匹配主链路。任一开关关闭后，新任务会被拒绝，正在等待对应阶段的任务会暂停，系统不会回退旧流水线。素材匹配只做二元自动判定：综合分达到采用线时继承素材组标签，否则不打标签，不提供 Shadow 或人工改判路径。

对象存储上传完成后，Worker 会读取对象实际大小；大于 `MAX_IMAGE_SIZE_MB` 的文件会在下载和解码前被拒绝。超过 `MAX_IMAGE_PIXELS`（默认 12,000,000 像素）但未超过 `MAX_IMAGE_DECODE_PIXELS`（默认 25,000,000 像素）的正常大图会生成按比例缩小的内部处理副本，原图保持不变；只有超过硬解码上限的异常大图才会在 OCR、LaMa 或其他高内存处理前拒绝。

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
