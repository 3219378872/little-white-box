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
| [README.md](README.md) | 面向读者的项目介绍、三仓职责与上手导航，不替代本文件规则或子仓正式知识 |
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
  `A00` 验收编号形态。条款定义与 Markdown 解析由所属子仓负责，根仓不重复解析子仓文档。
- 子仓公开 `make knowledge-export REF=<sha>`：使用当前工具读取固定提交 Git blob，输出
  `schema_version: 1` JSON（仓库、完整 revision、正式文档、approved SPEC 条款及定义指纹、跨仓引用）。
  根仓验证协议、唯一目标、gitlink 和提交可达性；导出不得执行历史脚本或改变子仓 Git 状态。
- 根 EVD 只存 `observed_commit`；两端版本从该提交 gitlink 推导。`coverage` 按
  `requirements: [repo:ID]` 与 `paths: [根仓相对输入路径]` 分组，规则见
  [证据说明](deploy/dev/e2e/evidence/README.md)。输入变化只使相关组过期，不改写历史结果。

## 联调入口

稳定端口、命令、运行产物、测试数据及权限边界见 [本地联调](deploy/dev/README.md)。
现场备忘仍以 [NOTES.md](NOTES.md) 为入口。本文不复制操作细节；修改编排时同步该说明。

## 根仓库修改与提交流程

范围：根仓只修改自己跟踪的文件（`README.md`、`justfile`、`deploy/dev/**`、`AGENTS.md`、`NOTES.md`、
`.gitignore`、`.gitmodules`）以及子仓 gitlink。任何子仓内容的改动都在对应子仓内按其流程完成，
根仓提交不得夹带子仓文件内容（只允许 160000 指针）。

分类：

- 纯文档（本文件、`NOTES.md`）：确认主检出 main 干净后可直接编辑提交。
- `README.md` 与其余编排资产（`justfile`、`deploy/dev/**`、`.gitignore`、`.gitmodules`）：必须走 task
  工作树流程；README 不适用上述直接编辑 main 的纯文档豁免。

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
