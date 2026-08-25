# AGENTS.md

这是小白盒前后端并排工作区。根目录是一个本地编排仓（当前无 remote），只跟踪联调编排资产；
两个子目录是各自独立的 git checkout，被根 `.gitignore` 忽略。本文件是工作区唯一规则入口，
负责路由与根资产约束；子仓规则一律以子仓自己的 `AGENTS.md` 为准，本文件不复制其内容。

## 子仓路由

| 目录 | 说明 | 规则入口 |
| --- | --- | --- |
| [little-white-box-content-community](little-white-box-content-community/) | Go / go-zero 后端 | [little-white-box-content-community/AGENTS.md](little-white-box-content-community/AGENTS.md) |
| [little-white-box-front](little-white-box-front/) | Flutter 前端 | [little-white-box-front/AGENTS.md](little-white-box-front/AGENTS.md) |

- 后端、前端任务先读对应子仓 `AGENTS.md`；正式知识从各自 `docs/knowledge/README.md` 按需加载，
  不遍历目录。
- 启动、联调、排障先看 [NOTES.md](NOTES.md)（现场备忘，非规范），再下到对应子仓。
- 跨端契约改动先核对后端 `.api`/`.proto` 与前端 `vendor/sdk_source` ↔ `lib/sdk` 的对应关系；
  两侧改动分别走各自子仓流程，根仓不代改。

## 根仓库资产与约束

| 文件 | 职责 |
| --- | --- |
| [justfile](justfile) | 本地栈唯一命令入口；recipe 是薄壳，source [deploy/dev/stack.sh](deploy/dev/stack.sh) 后调用其中函数 |
| [deploy/dev/stack.sh](deploy/dev/stack.sh) | 被 source 的函数库，不要直接执行；路径、端口、容器名均可用环境变量覆盖（`BACKEND`、`FRONTEND`、`RUN_DIR`、`ETC_DIR`、`PROXY_NAME` 等），默认值集中在文件头 |
| [deploy/dev/middleware-override.yml](deploy/dev/middleware-override.yml) | 叠加在后端 compose 之上的本地覆盖：端口重映射（Grafana→33000、SeaweedFS 卷 HTTP→18080）与 RocketMQ cgroup v2 规避参数 |
| [deploy/dev/proxy.conf](deploy/dev/proxy.conf) | :3002 同源入口 nginx 配置（`/`→前端 :3003，`/api/`→Gateway :8888，`/xbh-media/`→SeaweedFS S3 :8333）；容器 `xbh-dev-proxy` 以 `--network host` 运行 |
| [deploy/dev/seed_dev_user.sql](deploy/dev/seed_dev_user.sql) | 测试账号种子；eval 语料与生成/灌库脚本已迁至后端仓 `eval/`、`scripts/`（见下「运行时产物与数据」） |
| [deploy/dev/e2e/](deploy/dev/e2e/) | 黑盒 e2e 套件（pytest，对真实联调栈 `:3002` 跑 107 用例；`just e2e`） |

约束：

- 密钥只存在于 `/tmp/xbh-dev.env` 或 `deploy/dev/.env`（前者优先），两者都不进仓库；
  `deploy/dev/.env` 必须保持被 `.gitignore` 忽略。
- 修改 `stack.sh` 时保持函数式结构，兼容 `bash -euo pipefail`；改动后至少跑
  `bash -n` 与 `just status` 验证。

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

- `just up` / `just down`（alias `start` / `stop`）：全量起停 = `middleware-up` + `app-up`
- `just restart`；`just status`：容器、进程 pid 存活与关键端口探测
- `just seed` = `seed-dev-user` + `seed-eval-corpus`，均可单独执行
- 分步控制：`middleware-up/down` 只管 Docker 中间件（保留数据卷）；`app-up/down` 只管
  本机进程与反代；`infer-up/down` 管可选算法服务（compose profile `algorithm`：
  embedding-service + online-infer，首次启动需下载模型权重，未启动时推荐走规则降级）

### 运行时产物与数据

- 进程 pid 与日志在 `/tmp/xbh-run/{pids,logs}`；服务配置覆盖副本在 `/tmp/xbh-etc`
  （复制仓库 yaml，把 RPC `ListenOn`、网关 `RestConf` 与各服务 `DevServer` 的
  `Host: 0.0.0.0` 改写为回环地址，不改子仓原文件）。
- 测试账号 `admin` / `123456`；eval 语料 id 1001–1300 来自后端仓 `eval/corpus.json`，
  可选批量语料 id 2001–4000 来自后端仓 `eval/dev/corpus_2000.json`
  （`make gen-eval-posts` 重新生成）；搜索索引落后时 `app-up` 自动 rebuild。
- `middleware-up` 每次对后端仓 `deploy/sql/patches/*.sql` 做幂等重放（补丁必须自幂等，
  约定见该目录 README）；基线 schema 仅空卷初始化时经 initdb.d 生效。
- 重启机器后 `/tmp` 产物与反代容器消失，重新 `just up` 即可。
- CanvasKit 由前端 dev server 从 `<front>/web/canvaskit/`（编排层符号链接到 SDK 缓存，
  随升级自动跟随）同源提供，经 `--dart-define` 注入；SDK 缺失时回退 gstatic 并打警告。
- CanvasKit 由前端 dev server 从 `<front>/web/canvaskit/`（编排层符号链接到 SDK 缓存，
  随升级自动跟随）同源提供，经 `--dart-define` 注入；SDK 缺失时回退 gstatic 并打警告。
- 易变踩坑细节一律看 [NOTES.md](NOTES.md)，本文件只维护上述稳定事实。

## 根仓库修改与提交流程

范围：根仓只修改自己跟踪的文件（`justfile`、`deploy/dev/**`、`AGENTS.md`、`NOTES.md`、
`.gitignore`）。任何子仓内容的改动都在对应子仓内按其流程完成，根仓提交不得夹带子仓内容。

分类：

- 纯文档（本文件、`NOTES.md`）：确认主检出 main 干净后可直接编辑提交。
- 其余编排资产（`justfile`、`deploy/dev/**`、`.gitignore`）：必须走 task 工作树流程。

task 工作树流程：

1. 确认主检出 `main` 工作树干净。
2. `git worktree add .worktree/task-<name> -b task/<name>`；所有编辑在该工作树内完成。
   注意：工作树内没有子仓 checkout，运行时验证不可在此进行。
3. 提交前静态检查：`bash -n deploy/dev/stack.sh`；`just --list` 可解析。
4. 回主检出同步最新 `main`（当前无 remote，pull 跳过）；在 task 工作树把任务分支 rebase 到
   最新 `main`，冲突在 task 工作树解决后复跑第 3 步检查。
5. 主检出将 `main` fast-forward 到任务提交，禁用 merge commit；确认无误后删除
   `.worktree/task-<name>` 与 `task/<name>` 分支。
6. 合并后在主检出做运行时验证：`just status` 与本次涉及的 recipe；发现问题则开新一轮修复流程。

提交信息沿用现有风格：英文 Conventional Commits，scope 用 `(dev)`，如 `feat(dev): ...`、
`fix(dev): ...`。当前无 remote，流程终点是本地 `main`，没有 push 步骤。

提交边界：只提交根资产；`deploy/dev/.env` 永不入库。
