# JM QQ Bot

一个独立、开箱即用的 QQ 官方群机器人。群成员 `@机器人 JM 作品ID` 后，机器人会下载内容、优先合成为 PDF、使用随机密码打包为 AES ZIP，再通过 QQ 群文件接口上传。

本项目从 OOPZ Music Bot 的 JM/QQBOT 模块中独立出来，不依赖 OOPZ、QQ 音乐服务或内部桥接 API。

## 功能

- 单个或批量任务：`JM 111111 222222`，自动去重并顺序执行
- 下载前查询页数并估算耗时
- PDF 质量降级；仍超限时自动回退到原始图片 ZIP
- 每个任务生成独立的 14 位随机解压密码
- 群和用户双白名单、消息去重、单任务锁
- 下载/上传超时、上传重试、失败文件延迟清理
- Docker Compose 一条命令启动
- 可选 Cloudflare Containers 部署

## 最快启动：Docker Compose

前置条件：一个已开通群消息事件和群文件能力的 QQ 官方机器人，以及 Docker Compose。

```bash
cp .env.example .env
# 编辑 .env，至少填写 QQBOT_APP_ID 和 QQBOT_APP_SECRET
docker compose up -d --build
docker compose logs -f bot
```

建议同时填写：

- `QQBOT_ALLOWED_GROUP_OPENIDS`：允许使用机器人的群 OpenID，逗号分隔。
- `QQBOT_JM_ALLOWED_USER_OPENIDS`：允许下载的成员 OpenID，逗号分隔。

两项留空都表示不限制，不建议公开机器人时这样配置。

机器人上线后，在目标群发送：

```text
@机器人 JM 123456
@机器人 JM 123456 234567
```

任务数据挂载在 `./data`，成功上传后立即删除；上传失败的成品默认保留 30 分钟后删除，便于排障。

## 本地运行

需要 Python 3.11+ 和 Node.js 18+：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
npm ci --prefix uploader
cp .env.example .env
jm-qqbot
```

Windows PowerShell 激活命令为 `.\.venv\Scripts\Activate.ps1`。

## Cloudflare：能否不买服务器？

可以，但应使用 **Cloudflare Containers**，不是只用普通 Workers/Pages。

普通 Worker 没有适合本项目的本地运行时和临时磁盘，128 MB 内存也很难完成 Pillow/PDF/ZIP 处理；Free 计划每次请求仅 10 ms CPU。Containers 能直接运行本项目的 Python + Node Docker 镜像，带临时磁盘并允许出站网络，因此技术上可行。它属于 Workers Paid，仍按容器资源和网络出口计费，只是无需自行维护 VPS。

本项目的 QQ SDK 使用持续网关连接，而 Container 会在无入站活动后休眠。`cloudflare/wrangler.jsonc` 配置了每 5 分钟的 Cron 健康检查，并将休眠窗口设为 10 分钟，以维持一个单例容器；平台重启或部署期间，SDK 会重新连接。容器磁盘是临时的，所以不要把它当永久文件存储。

### 部署到 Cloudflare Containers

要求：Workers Paid 计划、本机 Docker、Node.js。首次部署：

```bash
cd cloudflare
npm install
npx wrangler login
npx wrangler secret put QQBOT_APP_ID
npx wrangler secret put QQBOT_APP_SECRET
npm run deploy
```

部署完成后访问 Worker 的 `/healthz`，会启动单例容器；Cron 随后负责保活。白名单和限制可在 `cloudflare/wrangler.jsonc` 的 `vars` 中修改后重新部署。

注意：

- 配置使用 `basic` 实例（1 GiB 内存、4 GB 临时磁盘）；大作品仍可能超出资源限制。
- Containers 不是免费服务，也不是永久免费进程。费用和平台限制可能变化，部署前查看 Cloudflare 最新定价。
- QQ 平台可能限制主动消息、文件大小、每日上传量和过期消息关联；实际权限以机器人后台为准。
- 如果追求最低成本和最稳定的常驻网关连接，小型 VPS/家用 NAS 仍通常更合适。

## 配置

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `QQBOT_APP_ID` | 无 | QQ 机器人 App ID，必填 |
| `QQBOT_APP_SECRET` | 无 | QQ 机器人 Secret，必填 |
| `QQBOT_ALLOWED_GROUP_OPENIDS` | 空 | 群白名单，空表示不限 |
| `QQBOT_JM_ALLOWED_USER_OPENIDS` | 空 | 用户白名单，空表示不限 |
| `QQBOT_JM_BATCH_MAX_ITEMS` | `3` | 单条命令最多作品数 |
| `QQBOT_JM_MAX_BYTES` | `83886080` | 成品最大字节数，不能超过 100 MiB |
| `QQBOT_JM_TIMEOUT_SECONDS` | `1200` | 单个下载/打包超时 |
| `QQBOT_JM_UPLOAD_TIMEOUT_SECONDS` | `900` | 文件上传超时 |
| `QQBOT_JM_FAILURE_RETAIN_SECONDS` | `1800` | 上传失败成品保留时间 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

## 开发与测试

```bash
python -m unittest discover -s tests -v
npm test --prefix uploader
ruff check jm_qqbot tests
```

## 安全与合规

- 不要提交 `.env`、App Secret、任务压缩包或日志。
- 公网机器人务必配置群/用户白名单，并留意带宽、磁盘和 QQ 上传额度。
- 仅下载、处理和分享你有权访问及传播的内容；使用者需自行遵守内容来源、QQ 平台及所在地法律的规则。

## License

[MIT](LICENSE)
