# 生产发布与恢复手册

生产服务器只拉取并运行已经由 CI 测试、构建和推送的版本化镜像，不在服务器现场构建代码或镜像。

## 发布前准备

`.env.production` 至少需要设置强随机值：`API_KEY`、`INTEGRATION_API_KEY`、`AUTH_SESSION_SECRET`、`AI_TAGGING_API_KEY`、`AI_CONFIG_ENCRYPTION_KEY`、`CALLBACK_SIGNING_SECRET`、PostgreSQL/MinIO 密码；并显式配置 `TRUSTED_HOSTS`、`CALLBACK_ALLOWED_HOSTS`、`API_IMAGE`、`API_GATEWAY_IMAGE`、`CLASSIFICATION_IMAGE`、`RENDER_IMAGE`、`WEB_IMAGE`。镜像必须使用 Git SHA、版本号或 digest，禁止使用 `latest`。

以下强制工作流开关必须全部为 `true`：

```dotenv
COMPLETION_ROUTING_ENABLED=true
BATCH_FILTER_BARRIER_ENABLED=true
POST_FILTER_BEAUTIFY_PLAN_ENABLED=true
LIBRARY_IMAGE_ONLY_MATCHING_ENABLED=true
LIBRARY_ONLY_TAGS_ENABLED=true
COMBINED_CLASSIFY_FILTER_ENABLED=true
EARLY_SEMANTIC_BRANCH_ENABLED=true
```

任一开关关闭时 `/health/ready` 返回失败，新任务会被拒绝，不能以关闭开关的方式跳过阶段。素材匹配只保留自动二元判定；上线前必须用离线标注集校准采用线，线上不提供 Shadow 或人工改判路径。

分支过滤响应约束建议保持以下默认值：

```dotenv
AI_PROCESSING_STRICT_JSON_SCHEMA_ENABLED=true
AI_PROCESSING_SCHEMA_MAX_RETRIES=0
AI_PROCESSING_MAX_COMPLETION_TOKENS=3000
```

系统对每张图片的每个 AI 节点只请求一次。供应商拒绝 Schema、响应缺字段、类型错误、返回无效 JSON，或单张图片在节点内超时时，只终止当前图片并记录失败节点与错误码；同批其他图片继续执行。整批处理完后，只要存在成功或业务拒绝结果，任务进入 `partial_failed` 并照常回调；仅全部图片失败时任务才进入 `failed`。诊断仍会保存到 `image_items.ai_processing_diagnostic_json`；Bearer 凭据和图片 Base64 不会保存。排障后只对明确选中的失败图片执行人工重试，禁止批量自动重试历史失败任务。只有任务截止时间、排序屏障等无法归属到单张图片的全局故障才会停止整批任务。

正式公网入口必须在外部负载均衡器或 API Gateway 终止 HTTPS。不要把 Compose 的 HTTP、PostgreSQL、Redis 或 MinIO 内部端口直接暴露到公网。

## 服务器只读前置检查

```bash
uptime
free -h
swapon --show
df -h
df -ih
docker system df
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

根盘、Docker 数据盘或目标盘达到 85%，inode 达到 85%，可用磁盘不足 10 GiB/20%，可用内存不足 2 GiB/25%，或最近 5 分钟负载超过 CPU 核心数时停止发布。先排查资源或异常任务，不自动清理、不自动重试。

## 标准发布

先记录当前镜像引用作为回滚版本，再设置新版本镜像引用：

```dotenv
API_IMAGE=ghcr.io/<owner>/tuxiangshibie-api:<git-sha>
API_GATEWAY_IMAGE=ghcr.io/<owner>/tuxiangshibie-api:<git-sha>
CLASSIFICATION_IMAGE=ghcr.io/<owner>/tuxiangshibie-api:<git-sha>
RENDER_IMAGE=ghcr.io/<owner>/tuxiangshibie-api:<git-sha>
WEB_IMAGE=ghcr.io/<owner>/tuxiangshibie-web:<git-sha>
```

执行：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --remove-orphans
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl --fail --silent https://<service-host>/health/ready
```

`migrate` 一次性容器会在 API 和 Worker 启动前执行 `alembic upgrade head`。就绪检查只有在数据库连接及迁移版本、Redis、对象存储、API/AI 配置全部正常时才返回 200。

## 发布验证

- 确认 Web 未向浏览器注入 `API_KEY`，登录后会话 Cookie 为 HttpOnly；自助注册账号固定为操作员，管理员可以维护多账号，操作员无法进入账号管理。
- 确认启用中的过滤标准恰好有一条兜底分类，再创建仅含测试图片的正式任务。
- 核对新任务自动冻结全部启用标准，`routing_mode` 为 `streaming_v2`，且不会产生 `not_selected`。
- 确认一次视觉 AI 请求同时返回完整分类评估和命中标准的过滤结果，未再产生第二次分支过滤请求。
- 确认一张图片过滤通过后立即并行启动美化规划、内容分析和 OpenCLIP 预向量，不等待同批其他图片完成过滤；预向量状态为 `provisional`，不得触发素材匹配。
- 确认美化完成后从最终交付图刷新权威 OpenCLIP 向量，只有最终向量状态为 `completed` 或 `failed` 才允许进入素材匹配。
- 确认素材匹配使用预处理方向归一化图的向量和内容特征，最终标签仍只来自素材组人工标签；增强和匹配都完成前图片不得进入交付终态。
- 确认 `worker-classification`、`worker-beautify-plan`、`worker-redaction`、`worker-inpaint`、`worker-enhance`、`worker-render`、`worker-analysis`、`worker-openclip` 和 `worker-matching` 均健康；旧 `filtering` 队列由 `worker-classification` 兼容消费，不再保留独立过滤容器。
- 确认回调包含时间戳和 HMAC 签名，接收方完成签名、时效和幂等校验。
- 检查 API、控制 Worker、各处理 Worker 和 beat 的有限量日志，不输出完整环境变量或密钥。
- 检查结构化日志中的 `combined_classify_filter_requests_total`、`beautify_plan_failures_total`、`enhancement_failures_total`、`redaction_stage_total`、`redaction_stage_duration_ms`、`redaction_logo_detections_total`、`redaction_manual_review_total`、`embedding_failures_total`、`library_match_total` 和 `forbidden_llm_tag_write_total`；新任务不应出现 `filter_classification_requests_total`、`routed_filter_requests_total` 或 `filter_barrier_trigger_total`，最后一项必须为零。
- 确认 `worker-inpaint` 保持并发 1、4 GiB 内存上限并定期回收子进程；`worker-redaction` 只做 OCR/水印检测，`worker-enhance` 只做普通美化，`worker-render` 负责 Logo 与最终编码。水印处理的 `outside_roi_changed_pixels` 必须为 0，Logo 未检出必须报告 `not_detected` 而非 `applied`。

## 回滚

把 `.env.production` 中的 `API_IMAGE`、`API_GATEWAY_IMAGE`、`CLASSIFICATION_IMAGE`、`RENDER_IMAGE` 和 `WEB_IMAGE` 一起恢复为发布前记录的版本，然后执行：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --remove-orphans
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl --fail --silent https://<service-host>/health/ready
```

数据库迁移默认只向前兼容。需要数据库降级时必须先评估数据损失并单独审批，不能在自动回滚中直接执行 `alembic downgrade`。

## 备份与恢复

必须将 PostgreSQL 逻辑备份、MinIO 桶备份和 `.env.production` 的加密密钥备份保存到服务器之外的受控位置。Redis AOF 只保证容器重建时运行时配置不丢失，不替代异地备份。至少按以下频率执行并保留恢复证据：

- PostgreSQL：每日全量备份；业务允许时增加更短周期或 WAL/PITR。
- MinIO：每日增量同步或使用对象存储版本控制/复制。
- 密钥与部署配置：每次变更后备份，限制访问并记录轮换时间。
- 每季度在隔离环境完成一次 PostgreSQL、MinIO 和配置联合恢复演练。

没有通过恢复演练的备份不能视为可用备份。
