---
id: EVD-20260906-cross-repository-knowledge
status: active
result: partial
updated_at: 2026-09-06
observed_commit: 33364ba5cee1434b1f1dfd7254ff847d096f65c9
commands:
  - just knowledge-check
  - BACKEND_GENERATE_PYTHON=/tmp/esx-embedding-proto-venv/bin/python3 just contract-check
  - bash -n deploy/dev/stack.sh
  - just --list
  - just up
  - just status
  - just e2e-agent-research
  - just e2e
  - just e2e deploy/dev/e2e/test_assistant.py::test_watch_matcher_delivers_assistant_message
coverage:
  - requirements:
      - little-white-box-front:FQ-002
      - little-white-box-front:FQ-008
    paths:
      - justfile
      - deploy/dev
      - little-white-box-content-community
      - little-white-box-front
scope:
  - static
  - integration
  - e2e
  - synthetic
external_upstream:
  - little-white-box-content-community@58735970348ee2258a45058e8f85c21eb0fb4963:IMP-community-core
---

# 跨仓知识链与联调证据

本页记录根编排提交 `33364ba5cee1434b1f1dfd7254ff847d096f65c9` 及其两个子仓 gitlink
共同完成后的验证结果。重构建立 `intent -> spec -> design -> implementation <-> evidence`
治理链和跨仓门禁；本轮没有修改业务 API、proto、SQL 或运行时语义。

2026-09-07 元数据迁移：子仓版本由观察提交的 gitlink 推导，coverage 保守保留整套编排及两端输入。
下列命令、结果和开放门禁仍属于原观察提交；迁移不构成重跑，也不把历史 partial 提升为当前通过证明。

## 已通过门禁

| 门禁 | 观察结果 |
| --- | --- |
| `just knowledge-check` | 根仓 71 个 fixture、后端 88 个 fixture 和前端知识图谱检查通过；390 个目标 ID、8 个跨仓引用可解析 |
| `BACKEND_GENERATE_PYTHON=/tmp/esx-embedding-proto-venv/bin/python3 just contract-check` | 后端临时 clone 重生成无漂移；前端两份 Gateway SDK 均为 current 且逐字节一致 |
| `bash -n deploy/dev/stack.sh`、`just --list` | 根编排脚本语法和命令入口解析通过 |
| `just up`、`just status` | 联调栈启动完成；`:3002` 页面和 `:3003` 前端均返回 200，`assistant-agent` 为 `ready`；算法 profile 未启动，运行在既定规则降级配置 |
| `just e2e-agent-research` | 4 项严格 synthetic 用例通过，耗时 7.84 秒 |

这些结果分别支持 `FQ-002` 的可重复契约同步和精确生成物比较，以及 `FQ-008` 的稳定 ID、
层间引用、条款覆盖、实现证据双向关系和跨仓链接机器校验。

## 全量 E2E 开放门禁

`just e2e` 的实际结果为 116 passed、5 skipped、1 failed，耗时 252.97 秒。唯一失败是
`deploy/dev/e2e/test_assistant.py::test_watch_matcher_delivers_assistant_message`；随后定向复跑仍为
1 failed，耗时 180.85 秒。

实时状态显示 Watch task 创建成功，post-create 已被 matcher 消费，`watch_execution` 为 `matched`，
`watch_hit` 已写入且 delivery bucket 已调度。对应模型 Watch run 最终为 `status=error`、
`error_code=ANSWER_VALIDATION_FAILED`，因此没有发布测试等待的未读 Assistant 消息。本证据将其保留为
真实模型回答校验的开放门禁，不用业务语义变更或放宽断言掩盖失败；因此总体结果为 `partial`，不能表述为
全量 E2E 通过。

## 未覆盖边界

本轮没有执行浏览器人工验收、实体设备、人工内容评审、容量或生产环境门禁。联调期间观察到配置模型的
上述失败，也不足以证明独立的 provider 兼容性或生产可用性，因此不声明 `browser`、`device`、
`human-review`、`live-provider` 或 `production` scope。
