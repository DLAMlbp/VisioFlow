# 生产发布与恢复手册

生产服务器只拉取并运行已经由 CI 测试、构建和推送的版本化镜像，不在服务器现场构建代码或镜像。

## 发布前准备

`.env.production` 至少需要设置强随机值：`API_KEY`、`INTEGRATION_API_KEY`、`AI_TAGGING_API_KEY`、`AI_CONFIG_ENCRYPTION_KEY`、`CALLBACK_SIGNING_SECRET`、PostgreSQL/MinIO 密码；并显式配置 `TRUSTED_HOSTS`、`CALLBACK_ALLOWED_HOSTS`、`API_IMAGE`、`WEB_IMAGE`。镜像必须使用 Git SHA、版本号或 digest，禁止使用 `latest`。

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

- 创建一套仅含测试图片的正式任务，确认两套启动规则必须且只能命中一套。
- 核对结果页与历史记录的 `selected`、`rejected`、`not_selected` 数量一致。
- 确认过滤通过后生成增强图，AI 标签的 `source_object_key` 指向增强图。
- 确认回调包含时间戳和 HMAC 签名，接收方完成签名、时效和幂等校验。
- 检查 API、控制 Worker、各处理 Worker 和 beat 的有限量日志，不输出完整环境变量或密钥。

## 回滚

把 `.env.production` 中的 `API_IMAGE` 和 `WEB_IMAGE` 恢复为发布前记录的版本，然后执行：

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
