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
- 容器健康检查和崩溃自动重启

## 最快启动：Docker Compose

前置条件：一个已开通群消息事件和群文件能力的 QQ 官方机器人，以及安装了 Docker Compose 的 Linux 服务器、NAS 或其他常驻设备。

```bash
git clone https://github.com/ji333abc/jm-qqbot.git
cd jm-qqbot
cp .env.example .env
# 编辑 .env，至少填写 QQBOT_APP_ID 和 QQBOT_APP_SECRET
docker compose up -d --build
docker compose logs -f bot
```

更新到新版本：

```bash
git pull
docker compose up -d --build
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
