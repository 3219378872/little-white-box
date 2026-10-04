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
| `:9333` / `:8333` | SeaweedFS master / S3（匿名只读 `xbh-media`；广告私有桶 `xbh-ad-private` 不可匿名读） |
| `127.0.0.1:9027` / `:9028` | review-rpc / ad-rpc（DevServer `:9127` / `:9128`） |
| `127.0.0.1:9137` / `:9138` | review-worker / ad-mq 指标 |
| `127.0.0.1:9026` | 审核精排占位 sidecar `moderation-infer`（`infer-up` 才启动） |

`ENTRY_PORT`、`FRONT_PORT`、`GATEWAY_PORT` 是上述三项端口的唯一配置源（1–65535）。
`prepare_etc` 改写 Gateway 的直接 `RestConf.Port`，`proxy_up` 将
`deploy/dev/proxy.conf` 模板渲染到 `$ETC_DIR/proxy.conf` 后挂载；前端监听、就绪检查与媒体默认公开
URL 使用同一组值。自定义 `PROXY_CONF` 必须是包含 `@@ENTRY_PORT@@`、`@@FRONT_PORT@@`、
`@@GATEWAY_PORT@@` 的模板；可用 `PROXY_RUNTIME_CONF` 覆盖生成副本路径，不改源模板。
`PROXY_IPV6`（`auto`/`1`/`0`，缺省 `auto`）控制渲染副本是否保留 `listen [::]`；`auto` 仅在 Linux
procfs 显示主机无 IPv6 协议栈时去掉它，否则 nginx 会因 `Address family not supported` 退出。

### 命令

- `just test-dev`：使用根仓隔离知识 Python 发现 `deploy/dev/tests/` 的全部编排单测；不连接真实栈，
  不包含 `e2e/`。凭据、生命周期、进程身份与清理、readiness、前端启动及 fixture 分域验证。

- `just up` / `just down`（alias `start` / `stop`）：`up` 先停止现有应用，再执行
  `middleware-up` 的 schema patch，最后启动同一源码版本的应用；禁止带旧进程重放迁移。
  `down` 会停应用、反代、algorithm profile（embedding-service / online-infer）和默认中间件
  容器，保留数据卷。`up` 不启也不停 infer。
- HTTP/端口就绪等待的秒数是整个等待的单调时钟预算，探针与重试睡眠共用该预算；允许小数，
  0 表示立即超时，负数及非有限值无效。
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
  `make sdk-check` 并逐字节核对两份 Gateway SDK；生成需固定版本的 Kitex/protoc 插件及后端 `requirements-generate.txt` 中的 Python 依赖；缺少这些 Python 模块时可用
  `BACKEND_GENERATE_PYTHON` 指定 Python 可执行文件，或用 `GENERATE_PYTHON_BIN_DIR` 指定其 bin 目录
- 分步控制：`middleware-up/down` 只管 Docker 中间件（保留数据卷）；`app-up/down` 只管
  本机进程与反代；`infer-up/down` 管可选算法服务（compose profile `algorithm`：
  embedding-service + online-infer + moderation-infer，首次启动需下载模型权重，未启动时推荐走规则降级，
  审核级联把候选全部转人审）
- `just review-role grant <userId> <roles> [markets] [languages]` / `revoke <userId>`：经后端
  `app/review/rolectl` 授予或撤销审核角色（reviewer、qa、policy_admin、qualification_reviewer）并写审计；
  这是唯一授权路径，没有在线接口。e2e 审核员只授予演示市场 `ID` / `id`，与手工联调数据隔离。

### 运行时产物与数据

- `justfile` 继续 source `deploy/dev/stack.sh` 后调用原函数；该入口按固定清单加载 `lib/` 下的配置、
  环境、进程身份、进程控制、readiness、中间件、前端、fixture、检查与生命周期模块。默认值与服务
  数组位于 `lib/config.sh`；模块定位不受调用目录或业务 `ROOT` 覆盖影响。直接 source 模块不是公开入口。

- 进程二进制、pid 与日志在 `/tmp/xbh-run/{bin,pids,logs}`；pidfile 指向直接执行的服务二进制，
  服务配置覆盖副本在 `/tmp/xbh-etc`
  （复制仓库 yaml，把 RPC `ListenOn`、网关 `RestConf` 与各服务 `DevServer` 的
  `Host: 0.0.0.0` 改写为回环地址，不改子仓原文件）。
- `/tmp/xbh-run`、日志/pid 目录和 `/tmp/xbh-etc` 为 `0700`，日志为 `0600`；常驻维护器在单个
  stdout 日志超过 5 MiB 时 copy-truncate，并只保留一份 `*.log.1.gz`。
- `app-up` 会在启动前清空历史 `assistant-rpc`、`assistant-watch`、`assistant-agent` 运行日志；这些
  日志可能含用户输入、工具参数或内容摘要，不跨版本保留。清理前停止并等待旧日志维护器；
  清理、归档发布与截断共享目录锁，并删除这三个服务被中断轮转留下的临时归档。锁文件不随清理删除。
- 广告与审核权威库 `xbh_ad`（`DB_AD`）、`xbh_review`（`DB_REVIEW`）缺省由 `DB_CONTENT` 换库名推导；
  `middleware-up` 对存量数据卷重放这两个库的幂等基线后再授权，应用账号对 `xbh_review.audit_log`
  只有 SELECT/INSERT。dev 默认 `MODERATION_FIXTURE_ENABLED=1`，e2e 可用文案标记驱动占位精排分数。
- 测试账号 `admin` / `123456`；eval 语料 id 1001–1300 来自后端仓 `eval/corpus.json`，
  可选批量语料 id 2001–4000 来自后端仓 `eval/dev/corpus_2000.json`
  （`make gen-eval-posts` 重新生成）；搜索索引落后时 `app-up` 自动 rebuild。
- 全新数据卷的启动顺序：`wait_topics` 以后端 `deploy/rocketmq/init-topics.sh` 的 `TOPICS` 为唯一清单；
  search-rpc 启动时要求 ES 索引已存在，`app-up` 因此先启动 search-mq，等 `SEARCH_INDEX_URL`
  （缺省 `http://127.0.0.1:9200/xbh_posts`）返回 200 后再启动 search-rpc。
- e2e 会在 session 结束时软删除本轮经测试客户端创建且仍存在的帖子；平台没有测试用户删除接口，
  因此本轮注册的 `e2e<RUN_ID>*` 用户仍保留，必要时按明确 RUN_ID 单独治理。
  清理逐帖校验当前 revision；单项请求或解码失败会继续处理其余登记帖子，并在结束时汇总失败。
  摘要只包含帖子 ID、操作阶段、HTTP 状态或异常类型，不记录正文、令牌或异常 URL。
- `middleware-up` 每次对后端仓 `deploy/sql/patches/*.sql` 做幂等重放（补丁必须自幂等，
  约定见该目录 README）；基线 schema 仅空卷初始化时经 initdb.d 生效。
- `xbh_assistant` 由上述 patches 创建；`app-up` 在 `DB_ASSISTANT` 为空时从
  `DB_CONTENT` 替换 schema 名得到 DSN。app 使用独立 `APP_MYSQL_*` 账号且只具备七个业务 schema
  的 SELECT/INSERT/UPDATE/DELETE；E2E 使用不同的 `E2E_MYSQL_*` 账号且只有 SELECT，旧 `xbh`
  默认账号在授权收敛后删除。同一 `E2E_MYSQL_*` 身份也在 ClickHouse 中收敛为只读
  `xbh_analytics` 账号（SHA-256 摘要入 SQL），黑盒探针不再使用无限制的 `default` 用户；
  探针只接受单条只读语句。轮换凭据后需重跑 `middleware-up` 同步两库账号。
- 重启机器后 `/tmp` 产物与反代容器消失，重新 `just up` 即可。
- CanvasKit 由静态伺服层从 `<front>/web/canvaskit/`（编排层符号链接到 SDK 缓存，随升级
  自动跟随）同源提供，构建期经 `--dart-define` 注入；SDK 缺失时回退 gstatic 并打警告。
- `:3003/:3002` 对外提供的是 **release 构建静态包**（lib/ 变化后 `app-up` 自动重建，
  `FORCE_FRONT_BUILD=1 just app-up` 强制重建，已运行时也会先停前端再重建/重启；失败时保持停止，
  不提供旧包或构建中的半成品）。构建缓存记录输入路径/内容、编译参数和已初始化 SDK 的
  `bin/cache/flutter.version.json` 指纹；删除、重命名或 SDK 变化均失效。构建前后指纹必须相同，
  才会原子记录缓存标记；构建期间输入变化会报错，重跑 `app-up`。指纹检查不执行 Flutter 或下载 SDK。
  DDC 调试模式（`make dev-real`）在当前
  SDK 下访客引导会被 DWDS RunRequest 门控卡死且附着即崩溃，仅限本机排障手动使用。
- 易变踩坑细节一律看 [NOTES.md](../../NOTES.md)，本页只维护上述稳定事实。

普通 `/api/` 请求不在 nginx 缓冲完整请求体，直接交给 Gateway 执行绝对摄取期限；
`client_body_timeout 3s` 仅限制两次读取之间的空闲时间。媒体沿用独立上传预算，SSE 路由不变。

## 私信媒体上传

`/api/v1/media/image|video|audio` 使用认证 multipart 上传。图片与音频文件上限 10 MiB，
视频 100 MiB；反代额外保留 1 MiB 表单开销，超限返回 JSON 错误码 4001。音频支持 MP3/WAV/M4A，
视频按容器轨道识别；不承诺转码或所有客户端的编码兼容性。视频/音频上传使用稳定
`idempotencyKey`，消息发送另用独立幂等键。更换文件才建立新上传任务。

运行 `just e2e deploy/dev/e2e/test_media_messages.py` 验证经同源入口的上传、媒体引用校验、
重复请求、接收历史与超过 20 MiB 的视频。测试使用专门创建的测试账号及小型媒体样本。

浏览器回归需要安装 `requests`、`playwright` 并提供 Chromium。运行
`python deploy/dev/e2e/media_browser.py --output <目录>` 验证真实文件选择、上传、发送和历史；
`python deploy/dev/e2e/gateway_browser.py --output <目录>` 验证界面登录、过期凭据恢复、内容互动与
Assistant 重载订阅。后者用无效 access token 触发真实刷新，保留有效 refresh token，产物不保存令牌。
Assistant 检查依赖正常 worker/provider 配置；订阅重连通过不代表模型回答质量通过。

本地中间件默认子网已改为 `172.30.240.0/24`（动态池 `.128/25`，网关 `.1`），可同时覆盖
`XBH_NETWORK_SUBNET`、`XBH_NETWORK_IP_RANGE`、`XBH_NETWORK_GATEWAY`。旧 Docker 网络不能
原地修改 IPAM，需在停止应用后用同一 Compose project 重建网络和容器，保留所有 volumes。
不要通过只改容器地址掩盖旧容器保留的固定 IP；正常服务依赖 DNS/宿主机映射端口。

Flutter Web 的本地 XFile 流通过 blob URL 读取；CSP `connect-src` 允许 `blob:`，媒体仍上传到同源
Gateway。公开 `/xbh-media/` 资源请求不携带 API Bearer 头（对象存储不接受此认证格式）。

本地生成的 media-rpc 配置默认通过 `http://127.0.0.1:<ENTRY_PORT>/xbh-media` 发布文件，
与网页同源；对象存储的内部连接仍使用 8333。外部浏览器访问时，将 `MEDIA_PUBLIC_BASE_URL`
设为该浏览器使用的站点 origin 加 `/xbh-media`（例如 `https://dev.example/xbh-media`），
再重启应用；不得把内部 S3 地址当作浏览器资源地址。子仓配置模板不由根编排改写。
