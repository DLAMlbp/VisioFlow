# 从本地安全更新生产服务器

双击仓库根目录的 `一键更新服务器.cmd`，即可把当前本地工作区发布到图像打标服务器。入口会调用 `scripts/deploy-production.ps1`；脚本不会把源码复制到服务器，也不会在服务器执行构建。

发布流程为：

1. 读取当前 Git 版本和本地修改清单。
2. 在本地运行测试并构建 Docker 镜像。
3. 干净工作区使用完整 Git SHA；未提交工作区使用唯一临时标签。镜像推送后统一解析成不可变 digest。
4. 在服务器执行磁盘、inode、内存、负载、Docker 和内核日志安全检查。
5. 在服务器保存当前 `.env.production` 作为回滚快照。
6. 服务器拉取指定版本镜像并运行 `docker compose up -d --remove-orphans`。
7. 验证 Web、API 就绪接口和 Compose 服务状态；验证失败会自动回滚。

## 前置条件

- 本地已经安装 Git、Docker Desktop、PowerShell 7；发布前端时还需 Node.js/npm。
- 已使用 `docker login ghcr.io` 登录，并具备推送 `ghcr.io/zuixi01` 镜像的权限。
- 本机 SSH 主机指纹已写入 `known_hosts`。
- 默认密钥为 `%USERPROFILE%\.ssh\id_ed25519_47_111_188_85_codex`，也可通过参数指定。
- 建议先提交修改以保留完整审计记录，但不是一键发布的强制条件。未提交内容不会伪装成 Git SHA，生产服务器最终固定到镜像 digest。

首次使用时，每位发布人员仍需由管理员配置自己的 SSH 密钥和 GHCR 权限。不要复制或共享其他人的私钥。

## 常用命令

最简单的操作方式：

```text
双击：一键更新服务器.cmd
```

也可以在终端完整发布：

```powershell
pwsh -File .\scripts\deploy-production.ps1
```

只发布后端：

```powershell
pwsh -File .\scripts\deploy-production.ps1 -Component Api
```

只发布前端：

```powershell
pwsh -File .\scripts\deploy-production.ps1 -Component Web
```

只构建并推送镜像，不连接服务器：

```powershell
pwsh -File .\scripts\deploy-production.ps1 -BuildOnly
```

指定另一把 SSH 密钥：

```powershell
pwsh -File .\scripts\deploy-production.ps1 `
  -IdentityFile 'C:\Users\name\.ssh\production_key'
```

`-SkipTests` 只适合已经由同一 Git SHA 的 CI 完整验证过的紧急发布，不建议日常使用。

要求工作区必须干净、禁止未提交内容发布：

```powershell
pwsh -File .\scripts\deploy-production.ps1 -RequireCleanGit
```

## 安全熔断

出现下列情况时，脚本会在修改服务器之前停止：

- 磁盘或 inode 使用率达到 85%。
- 可用空间低于 10 GiB 或文件系统容量的 20%，以更严格者为准。
- `MemAvailable` 低于 2 GiB 或总内存的 25%，以更严格者为准。
- 最近 5 分钟负载高于 CPU 核心数。
- 检测到其他构建、备份或迁移任务。
- 最近两小时内核日志出现 OOM、I/O、文件系统错误或 panic。

脚本不会执行镜像清理、Docker 重启、服务器重启、远程构建或数据库迁移。若版本包含数据库迁移，应走单独的、经过确认的迁移流程。

每次成功发布都会在服务器的 `/opt/image-intelligence/.deployments/` 保存权限为 `600` 的环境快照，并在终端输出对应的精确回滚命令。快照保留在服务器，不会输出其中的环境变量或密钥。
