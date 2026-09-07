---
id: EVD-20260907-knowledge-structure
status: active
result: passed
updated_at: 2026-09-07
observed_commit: 9f6761b05ee083b23ac2228e5231cec4cdba1e99
commands:
  - just knowledge-check
  - BACKEND_GENERATE_PYTHON=/tmp/esx-embedding-proto-venv/bin/python3 just contract-check
  - bash -n deploy/dev/stack.sh
  - just --list
  - ruff check deploy/dev/workspace_checks.py deploy/dev/test_workspace_checks.py
  - git diff --check
  - just status
scope:
  - static
  - unit
coverage:
  - requirements:
      - little-white-box-front:FQ-002
    paths:
      - justfile
      - deploy/dev/stack.sh
      - deploy/dev/workspace_checks.py
      - deploy/dev/requirements-knowledge.txt
      - little-white-box-content-community/Makefile
      - little-white-box-content-community/scripts/generate.sh
      - little-white-box-content-community/scripts/requirements-generate.txt
      - little-white-box-content-community/app
      - little-white-box-content-community/proto
      - little-white-box-content-community/go.mod
      - little-white-box-content-community/go.sum
      - little-white-box-front/Makefile
      - little-white-box-front/tools/sync_gateway_sdk.py
      - little-white-box-front/vendor/sdk_source
      - little-white-box-front/lib/sdk
  - requirements:
      - little-white-box-front:FQ-008
    paths:
      - justfile
      - deploy/dev/stack.sh
      - deploy/dev/workspace_checks.py
      - deploy/dev/test_workspace_checks.py
      - deploy/dev/requirements-knowledge.txt
      - little-white-box-content-community
      - little-white-box-front
external_upstream:
  - little-white-box-content-community@8ae6dfa773898af81462440b8004369313db45f1:IMP-community-core
---

# 知识结构重构跨仓验收

本页记录三仓 fast-forward 整合后的根提交。两端版本由 observed_commit 的 gitlink 推导；本证据不
拥有子仓 IMP，不改变产品、API、proto、SQL、SDK 或运行时语义。知识工具依赖已通过
`just knowledge-setup` 分别安装到三个仓库被忽略的隔离环境，未修改系统 Python。

## 实际结果

| 门禁 | 结果 |
| --- | --- |
| `just knowledge-check` | exit 0；46 项根仓测试、40 项后端知识测试、11 项后端策略测试通过；前端 59 份文档、54 条条款通过；gitlink 与两端 HEAD 一致，9 个固定跨仓引用均可解析 |
| `just contract-check`（显式生成 Python） | exit 0；一次性后端 clone 重生成零漂移；前端 SDK 重生成无漂移，vendor 与 lib 两份文件逐字节相同，受跟踪工作树未被修改 |
| `bash -n`、`just --list` | exit 0；脚本语法与薄命令入口有效 |
| `ruff check ...`、`git diff --check` | exit 0 |
| `just status` | exit 0；既有应用进程存活，assistant-agent 报 ready，页面入口返回 200，可选算法服务未启动；仅作运行现场观察，不作为业务接口验收 |

知识与契约验收在主检出执行，静态命令在相同提交执行。根检查器消费子仓当前工具导出的固定提交 JSON，
不解析子仓 Markdown，也不执行历史脚本。旧跨仓 partial 记录正常报告输入过期，仍保留原命令与结果。
知识组需要校验两端全部当前映射、源码路径、链接和历史证据，因此保守使用完整子仓输入；契约组单独
记录生成输入，纯文档更新不会自动使契约组失效。

两端基线比较确认，248 条后端与 54 条前端批准条款的定义、INT/SPEC 文件、IMP 责任归属、主设计及
原有 gap 均未改变。最终后端仍为 194 aligned / 50 unknown / 4 diverged，前端仍为
25 aligned / 28 unknown / 1 diverged。前端受工具变更影响的平台组已在提交后重新验证并记录本仓 EVD。

## 未覆盖边界

本轮没有执行 `just up`、全量 E2E、浏览器、实体设备、真实 provider、容量或生产验证，没有更改服务
配置、凭据或数据。`just status` 的存活观察不证明这些范围通过。原记录中的 Watch 模型回答校验失败、
真实接口与浏览器门禁等继续开放；结构迁移和本地检查不关闭这些门禁。
