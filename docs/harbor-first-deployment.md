# Harbor 首次部署

本说明对应后端镜像 `harbor.dangjia.com/base/visioflow:v20260905`。
该镜像地址由交付方提供；仓库配置不能证明 Harbor 中的镜像已存在或与源码兼容。
前端独立发布到 `harbor.dangjia.com/base/visioflow-web:v20260905`。
模板中的前端地址是发布目标，必须在发布成功后才能使用。

## 1. 在 GitLab 发布前端

项目新增 `.gitlab-ci.yml`，默认分支的流水线提供手动任务 `publish-harbor-web`。
运维需配置一个支持 privileged Docker-in-Docker 的 Docker executor Runner，
并在 Runner 配置中共享 `/certs/client` TLS 证书卷。
在项目 CI/CD Variables 中设置 `HARBOR_USERNAME` 和 `HARBOR_PASSWORD`，
密码设为 masked；使用 protected 变量时需在受保护分支运行。
凭据应仅允许推送 `base/visioflow-web`，不要写入 Git 文件或应用 `.env.production`。

在 GitLab 的“构建 → 流水线”中选择最新 main 流水线，手动运行该任务。
任务先构建并测试前端、检查 Nginx，再推送镜像；不需要真实应用 API Key。
如目标标签已存在，任务拒绝覆盖，请设置新的 `WEB_RELEASE_TAG` 并同步部署配置。
没有可用 Runner 或 Harbor 凭据时，该任务不会完成发布，不能直接继续启动。
发布成功也不代表与已有后端的端到端兼容性验证已完成。

## 2. 客户服务器配置

在客户已有源码目录更新到包含本说明的版本，保留现有 `.env.production`。
首次创建配置：

```bash
cd /dangjia/project/visioflow
if [ ! -f .env.production ]; then
  cp .env.production.example .env.production
fi
chmod 600 .env.production
nano .env.production
```

填写客户自己的 API Key、数据库/MinIO 密码、域名、回调配置。
原有配置文件不会因 Git 更新而自动同步镜像变量；编辑其现有行：

```dotenv
API_IMAGE=harbor.dangjia.com/base/visioflow:v20260905
API_GATEWAY_IMAGE=harbor.dangjia.com/base/visioflow:v20260905
CLASSIFICATION_IMAGE=harbor.dangjia.com/base/visioflow:v20260905
RENDER_IMAGE=harbor.dangjia.com/base/visioflow:v20260905
WEB_IMAGE=harbor.dangjia.com/base/visioflow-web:v20260905
```

如果前端发布选择了其他标签，将 WEB_IMAGE 改为实际发布结果。
不能把后端镜像填到 WEB_IMAGE。

## 3. 校验、启动和查看日志

`customer-compose.sh` 自动定位项目并添加 `--env-file .env.production`，
避免 `required variable API_IMAGE/API_GATEWAY_IMAGE/WEB_IMAGE is missing`。
Compose 服务内的 `env_file` 只负责容器环境，不代替 CLI 的变量加载参数。

```bash
docker login harbor.dangjia.com
bash scripts/customer-compose.sh config --quiet
bash scripts/customer-compose.sh pull
```

只有以上命令成功，且完成 [生产发布预检](production-runbook.md) 后才执行：

```bash
bash scripts/customer-compose.sh up -d
bash scripts/customer-compose.sh ps --all
bash scripts/customer-compose.sh logs --tail 100
curl --fail --silent --show-error http://127.0.0.1:8088/health/ready
```

若修改了 WEB_PORT，请相应调整健康检查地址。最后用少量图片验证提交、处理、结果和回调。
`manifest unknown` 表示对应标签尚未发布或填写错误；`unauthorized` 表示 Harbor 登录/读取权限不足。
配置校验不检查镜像是否存在，`pull` 成功也不代替健康检查和端到端验收。
更新已有环境前按生产手册备份配置与数据；生产服务器不执行源码构建。
