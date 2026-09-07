# 本地联调

本页保存根编排仓的稳定操作事实，路径以工作区根目录为基准。规则与提交流程见
[AGENTS.md](../../AGENTS.md)，现场排障先看 [NOTES.md](../../NOTES.md)。

## 联调稳定事实

### 入口与端口

| 地址 | 用途 |
| --- | --- |
| `http://127.0.0.1:3002` | 对外同源入口：页面 + `/api` + `/xbh-media` |
| `127.0.0.1:3003` | Flutter 开发服，仅本机，不直接对外 |
| `127.0.0.1:8888` | Gateway |
| 宿主机 `:33000` | Grafana（容器内 3000） |
| 宿主机 `:18080` | SeaweedFS 卷 HTTP（容器内 8080） |
| `:9333` / `:8333` | SeaweedFS master / S3 |

### 命令

- `just up` / `just down`（alias `start` / `stop`）：`up` 先停止现有应用，再执行
  `middleware-up` 的 schema patch，最后启动同一源码版本的应用；禁止带旧进程重放迁移。
  `down` 会停应用、反代、algorithm profile（embedding-service / online-infer）和默认中间件
  容器，保留数据卷。`up` 不启也不停 infer。
- `just restart`：只反弹应用与默认中间件，已在跑的 infer 保持不动；`just status`：容器、
  进程 pid 存活与关键端口探测（含 `:50051` / `:9025`）
- `just rotate-db-credentials`：只轮换本地 app/E2E MySQL 凭据并原子改写 env，不输出新值；
  下一次 `middleware-up` 创建独立账号并撤销旧默认账号
- `just seed` = `seed-dev-user` + `seed-eval-corpus`，均可单独执行
- `just knowledge-setup`：为三个仓库安装固定版本的隔离知识工具依赖，不修改系统 Python。
  根检查器可用 `KNOWLEDGE_PYTHON` 覆盖；子仓分别用 `BACKEND_KNOWLEDGE_PYTHON`、
  `FRONTEND_KNOWLEDGE_PYTHON` 覆盖，根解释器不向两端泄漏。
- `just knowledge-check`：先运行根检查器单测，再核对两个 gitlink 与子仓 HEAD、校验固定提交上的
  跨仓引用，并调用后端 `make engineering-lint` 与前端 `make knowledge-check`
- `just contract-check`：在一次性本地 clone 中运行后端 `make generate` 并要求零差异，再调用前端
  `make sdk-check` 并逐字节核对两份 Gateway SDK；缺少 `grpc_tools.protoc` 时可用
  `BACKEND_GENERATE_PYTHON` 指定 Python 可执行文件，或用 `GENERATE_PYTHON_BIN_DIR` 指定其 bin 目录
- 分步控制：`middleware-up/down` 只管 Docker 中间件（保留数据卷）；`app-up/down` 只管
  本机进程与反代；`infer-up/down` 管可选算法服务（compose profile `algorithm`：
  embedding-service + online-infer，首次启动需下载模型权重，未启动时推荐走规则降级）

### 运行时产物与数据

- 进程二进制、pid 与日志在 `/tmp/xbh-run/{bin,pids,logs}`；pidfile 指向直接执行的服务二进制，
  服务配置覆盖副本在 `/tmp/xbh-etc`
  （复制仓库 yaml，把 RPC `ListenOn`、网关 `RestConf` 与各服务 `DevServer` 的
  `Host: 0.0.0.0` 改写为回环地址，不改子仓原文件）。
- `/tmp/xbh-run`、日志/pid 目录和 `/tmp/xbh-etc` 为 `0700`，日志为 `0600`；常驻维护器在单个
  stdout 日志超过 5 MiB 时 copy-truncate，并只保留一份 `*.log.1.gz`。
- `app-up` 会在启动前清空历史 `assistant-rpc`、`assistant-watch`、`assistant-agent` 运行日志；这些
  日志可能含用户输入、工具参数或内容摘要，不跨版本保留。
- 测试账号 `admin` / `123456`；eval 语料 id 1001–1300 来自后端仓 `eval/corpus.json`，
  可选批量语料 id 2001–4000 来自后端仓 `eval/dev/corpus_2000.json`
  （`make gen-eval-posts` 重新生成）；搜索索引落后时 `app-up` 自动 rebuild。
- e2e 会在 session 结束时软删除本轮经测试客户端创建且仍存在的帖子；平台没有测试用户删除接口，
  因此本轮注册的 `e2e<RUN_ID>*` 用户仍保留，必要时按明确 RUN_ID 单独治理。
- `middleware-up` 每次对后端仓 `deploy/sql/patches/*.sql` 做幂等重放（补丁必须自幂等，
  约定见该目录 README）；基线 schema 仅空卷初始化时经 initdb.d 生效。
- `xbh_assistant` 由上述 patches 创建；`app-up` 在 `DB_ASSISTANT` 为空时从
  `DB_CONTENT` 替换 schema 名得到 DSN。app 使用独立 `APP_MYSQL_*` 账号且只具备七个业务 schema
  的 SELECT/INSERT/UPDATE/DELETE；E2E 使用不同的 `E2E_MYSQL_*` 账号且只有 SELECT，旧 `xbh`
  默认账号在授权收敛后删除。
- 重启机器后 `/tmp` 产物与反代容器消失，重新 `just up` 即可。
- CanvasKit 由静态伺服层从 `<front>/web/canvaskit/`（编排层符号链接到 SDK 缓存，随升级
  自动跟随）同源提供，构建期经 `--dart-define` 注入；SDK 缺失时回退 gstatic 并打警告。
- `:3003/:3002` 对外提供的是 **release 构建静态包**（lib/ 变化后 `app-up` 自动重建，
  `FORCE_FRONT_BUILD=1 just app-up` 强制重建）。DDC 调试模式（`make dev-real`）在当前
  SDK 下访客引导会被 DWDS RunRequest 门控卡死且附着即崩溃，仅限本机排障手动使用。
- 易变踩坑细节一律看 [NOTES.md](../../NOTES.md)，本页只维护上述稳定事实。

