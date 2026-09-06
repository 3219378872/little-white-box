# AGENTS.md

这是小白盒前后端并排工作区。根目录是联调编排仓（远端
`git@github.com:3219378872/little-white-box.git`），只跟踪联调编排资产与两个子仓的
submodule 指针。本文件是工作区唯一规则入口，负责路由与根资产约束；子仓规则一律以子仓自己的
`AGENTS.md` 为准，本文件不复制其内容。

克隆需带上子仓：

    git clone --recurse-submodules git@github.com:3219378872/little-white-box.git

已有检出补初始化：`git submodule update --init --recursive`。子仓提交仍在各自仓库完成；
根仓只更新指针（`git add <submodule>`），不得把子仓文件当根仓内容提交。

## 子仓路由

| 目录 | 说明 | 规则入口 |
| --- | --- | --- |
| [little-white-box-content-community](little-white-box-content-community/) | Go / go-zero 后端 | [little-white-box-content-community/AGENTS.md](little-white-box-content-community/AGENTS.md) |
| [little-white-box-front](little-white-box-front/) | Flutter 前端 | [little-white-box-front/AGENTS.md](little-white-box-front/AGENTS.md) |

- 后端、前端任务先读对应子仓 `AGENTS.md`；正式知识从各自 `docs/knowledge/README.md` 按需加载，
  不遍历目录。
- 启动、联调、排障先看 [NOTES.md](NOTES.md)（现场备忘，非规范），再下到对应子仓。
- 跨端公开 REST 与 Flutter SDK 的生成源是后端
  `little-white-box-content-community/app/gateway/gateway.api`；后端 `.proto` 只生成内部 RPC
  契约。Dart 生成文件必须同步到前端 `vendor/sdk_source` 与 `lib/sdk`，两侧改动分别走各自子仓流程，
  根仓不代改。

## 根仓库资产与约束

| 文件 | 职责 |
| --- | --- |
| [.gitmodules](.gitmodules) | 子仓指针：`little-white-box-content-community`、`little-white-box-front`，均跟踪 `main` |
| [justfile](justfile) | 本地栈唯一命令入口；recipe 是薄壳，source [deploy/dev/stack.sh](deploy/dev/stack.sh) 后调用其中函数 |
| [deploy/dev/stack.sh](deploy/dev/stack.sh) | 被 source 的函数库，不要直接执行；路径、端口、容器名均可用环境变量覆盖（`BACKEND`、`FRONTEND`、`RUN_DIR`、`ETC_DIR`、`PROXY_NAME` 等），默认值集中在文件头 |
| [deploy/dev/middleware-override.yml](deploy/dev/middleware-override.yml) | 叠加在后端 compose 之上的本地覆盖：端口重映射（Grafana→33000、SeaweedFS 卷 HTTP→18080）与 RocketMQ cgroup v2 规避参数 |
| [deploy/dev/proxy.conf](deploy/dev/proxy.conf) | :3002 同源入口 nginx 配置（`/`→前端 :3003，`/api/`→Gateway :8888，`/xbh-media/`→SeaweedFS S3 :8333）；容器 `xbh-dev-proxy` 以 `--network host` 运行 |
| [deploy/dev/seed_dev_user.sql](deploy/dev/seed_dev_user.sql) | 测试账号种子；eval 语料与生成/灌库脚本已迁至后端仓 `eval/`、`scripts/`（见下「运行时产物与数据」） |
| [deploy/dev/workspace_checks.py](deploy/dev/workspace_checks.py) | 只读校验根 gitlink、跨仓知识引用、后端生成漂移及前端 Gateway SDK，不向受跟踪工作树写生成物 |
| [deploy/dev/e2e/](deploy/dev/e2e/) | 黑盒 e2e 套件（pytest，对真实联调栈 `:3002`；`just e2e` 全量，传 pytest 路径/过滤条件时只跑所选项） |
| [deploy/dev/e2e/evidence/](deploy/dev/e2e/evidence/) | 只存需要根仓及两个 gitlink 共同解释的跨仓联调证据，不承载子仓产品语义 |

约束：

- 密钥只存在于 `/tmp/xbh-dev.env` 或 `deploy/dev/.env`（前者优先），两者都不进仓库；
  `deploy/dev/.env` 必须保持被 `.gitignore` 忽略；加载时收紧为 `0600`。
- 修改 `stack.sh` 时保持函数式结构，兼容 `bash -euo pipefail`；改动后至少跑
  `bash -n` 与 `just status` 验证。
- 正式跨仓引用使用 front matter 列表 `external_upstream`，每项固定为
  `repo@<40位提交SHA>:<正式文档ID或已批准SPEC条款ID>`；repo 只能是根仓或上述两个子仓的仓库名。
  条款 ID 按目标仓分派：前端只接受 `FX-000` / `FQ-000` 形态，后端接受其大写域前缀、三位编号或
  `A00` 验收编号形态。正式定义只从正文可见的 `-`/`*` 反引号列表项，或首列表头为 `ID`、
  `requirement`、`条款` 且 separator 合法的至少两列表格读取；列表项冒号后必须有非空定义，表格
  ID 后至少一列必须有非空定义，表头和条款 cell 的可选反引号必须成对。
- 跨仓检查器先隔离顶层及列表容器内的 fenced code，再处理 inline code 与 HTML comment。inline
  backtick span 只由长度完全相同的 maximal run 闭合；跨行匹配不得越过空行、ATX/Setext 标题、
  fence 起始、任一新列表项或所在列表项的其他 deindent block。同一列表项达到 content indent
  的续行可以闭合。info string 含 backtick 的非法 backtick fence-shaped 行只允许同行匹配；fence 或
  code span 内的 comment marker 只按字面量处理。front matter、HTML comment、fenced code、普通
  正文引用和孤立表格行都不建立条款。

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
- 易变踩坑细节一律看 [NOTES.md](NOTES.md)，本文件只维护上述稳定事实。

## 根仓库修改与提交流程

范围：根仓只修改自己跟踪的文件（`justfile`、`deploy/dev/**`、`AGENTS.md`、`NOTES.md`、
`.gitignore`、`.gitmodules`）以及子仓 gitlink。任何子仓内容的改动都在对应子仓内按其流程完成，
根仓提交不得夹带子仓文件内容（只允许 160000 指针）。

分类：

- 纯文档（本文件、`NOTES.md`）：确认主检出 main 干净后可直接编辑提交。
- 其余编排资产（`justfile`、`deploy/dev/**`、`.gitignore`、`.gitmodules`）：必须走 task 工作树流程。

task 工作树流程：

1. 确认主检出 `main` 工作树干净。
2. `git worktree add .worktree/task-<name> -b task/<name>`；所有编辑在该工作树内完成。
   注意：工作树内没有子仓 checkout，运行时验证不可在此进行。
3. 提交前静态检查：`bash -n deploy/dev/stack.sh`；`just --list` 可解析。
4. 回主检出 `git pull --ff-only origin main`；在 task 工作树把任务分支 rebase 到
   最新 `main`，冲突在 task 工作树解决后复跑第 3 步检查。
5. 主检出将 `main` fast-forward 到任务提交，禁用 merge commit；确认无误后删除
   `.worktree/task-<name>` 与 `task/<name>` 分支。
6. 合并后在主检出做运行时验证：`just status` 与本次涉及的 recipe；发现问题则开新一轮修复流程。
7. 验证通过后 `git push origin main`。

提交信息沿用现有风格：英文 Conventional Commits，scope 用 `(dev)`，如 `feat(dev): ...`、
`fix(dev): ...`。

提交边界：只提交根资产与子仓指针；`deploy/dev/.env` 永不入库。
