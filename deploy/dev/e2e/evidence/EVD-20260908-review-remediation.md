---
id: EVD-20260908-review-remediation
status: active
result: passed
updated_at: 2026-09-08
observed_commit: 6c70d644a3d3138d684cd71abf588c7c0308dda8
commands:
  - bash -n deploy/dev/stack.sh
  - for review_module in deploy/dev/lib/*.sh; do bash -n "$review_module"; done
  - just --list
  - just test-dev
  - just knowledge-check
  - BACKEND_GENERATE_PYTHON=/tmp/little-review-codegen-nRmdnN/venv/bin/python3 just contract-check
  - E2E_RUN_ID=r0908fixfull1 just e2e
  - E2E_RUN_ID=r0908fixresearch1 just e2e-agent-research
  - E2E_RUN_ID=r0908fixreset1 just e2e-agent-reset
  - just status
  - curl --fail --silent --show-error http://127.0.0.1:3002/api/v1/health/ready
scope:
  - static
  - unit
  - integration
  - e2e
  - synthetic
  - live-provider
coverage:
  - requirements:
      - little-white-box-front:FQ-002
      - little-white-box-front:FQ-008
    paths:
      - justfile
      - deploy/dev/stack.sh
      - deploy/dev/lib
      - deploy/dev/tests
      - deploy/dev/workspace_checks.py
      - deploy/dev/requirements-knowledge.txt
      - deploy/dev/log_maintainer.py
      - deploy/dev/serve_release.py
      - deploy/dev/middleware-override.yml
      - deploy/dev/proxy.conf
      - deploy/dev/seed_dev_user.sql
      - deploy/dev/e2e/api_client.py
      - deploy/dev/e2e/conftest.py
      - deploy/dev/e2e/dbprobe.py
      - deploy/dev/e2e/poll.py
      - deploy/dev/e2e/support.py
      - deploy/dev/e2e/pytest.ini
      - deploy/dev/e2e/requirements.txt
      - deploy/dev/e2e/fixtures
      - deploy/dev/e2e/test_api_client_cleanup.py
      - deploy/dev/e2e/test_assistant.py
      - deploy/dev/e2e/test_assistant_research.py
      - deploy/dev/e2e/test_auth.py
      - deploy/dev/e2e/test_behavior.py
      - deploy/dev/e2e/test_comment.py
      - deploy/dev/e2e/test_feed.py
      - deploy/dev/e2e/test_health.py
      - deploy/dev/e2e/test_interaction.py
      - deploy/dev/e2e/test_journey.py
      - deploy/dev/e2e/test_media.py
      - deploy/dev/e2e/test_message.py
      - deploy/dev/e2e/test_post.py
      - deploy/dev/e2e/test_review_regressions.py
      - deploy/dev/e2e/test_search.py
      - deploy/dev/e2e/test_user.py
      - little-white-box-content-community
      - little-white-box-front
external_upstream:
  - little-white-box-content-community@0321e2568a6a3f62d0f8898db9e8ae6c87f2523b:IMP-assistant-agent
artifacts:
  - deploy/dev/e2e/evidence/assets/review-remediation-20260908/validation-summary.json
---

# 全面审查修复跨仓联调验收

本页记录审查修复后的根编排与两个子仓组合，子仓版本从观察提交的 gitlink 推导。上列命令均在
观察提交实际运行并取得 exit 0；证据自身随后独立提交。两端实现、单仓回归及条款状态由各自的
`EVD-20260908-review-remediation`、`EVD-review-remediation-2026-09-08` 与 IMP 持有。

## 最终验证

| 检查 | 实际结果 |
| --- | --- |
| Shell 与 recipe | 入口和 10 个 lib 模块全部通过 `bash -n`；`just --list` 可解析 |
| 根编排单测 | 131 项通过，14.707s；凭据轮换测试的全部运行目录和生命周期锁隔离在自己的临时目录 |
| 知识门禁 | 根 46 项、后端 40+11 项通过；前端 63 文档、54 条款校验通过，状态仍为 25 aligned / 28 unknown / 1 diverged；11 条既有跨仓引用可解析 |
| 生成契约 | 临时 clone 重生成后端无漂移，前端两份 Gateway SDK 均 current 且逐字节一致 |
| 正常配置全量套件 | 142 项收集，137 passed / 5 skipped，180.27s；通过项包含 14 项离线清理 mock 与 123 项真实栈测试 |
| 研究 fixture | 4 passed，6.95s；社区来源、外部来源、外部故障和非法引用情形均通过 |
| reset/replay fixture | 1 passed，1.11s；截断重试、response_reset 与持久事件重放通过 |
| 最终就绪 | :3002 页面与 :3003 页面均 200，worker alive ready，健康接口 ready 且 9 项依赖全部 ok |

全量套件中跳过的 5 项正是专用 fixture 的 1+4 项，已由上述两个 recipe 独立执行。`live-provider`
仅表示正常配置下基础生成、SSE 重连与 Watch 投递实际经过现有模型服务，不代表长研究质量验收。
fixture 的模型和外部检索响应来自严格本地替身，仍保留为 synthetic 范围。

## 新增回归与清理边界

`test_review_regressions.py` 的 6 项真实栈回归覆盖：只改标题时保留图片并递增 revision；显式空数组
清空图片；创建帖子时拒绝仅 URL 或 URL+mediaId 引用他人媒体；编辑拒绝未登记外链且不改变 revision；
非法推荐游标返回参数错误；游标拒绝身份、requestId、pageSize、scene、sessionId、experimentId
变化，同时原合法游标可继续返回非空、无重复的下一页。这组测试没有声称覆盖“编辑时引用他人有效
mediaId”的真实栈场景，该分支由后端局部回归承接。

清理 helper 仅处理本轮客户端登记的帖子，读取当前正整数 revision 后软删除。单项网络、JSON 或
HTTP 失败被归集后继续其余目标，最终由 session teardown 断言报告失败。14 项离线测试验证异常
隔离、畸形详情拒绝和已删除状态；摘要不包含响应正文、令牌或异常 URL。正常全量与两个 fixture
session 的清理均无失败。测试注册用户仍按现有机制保留，可用 RUN_ID 精确识别，未扩大清理目标。

## 启动与失败修复记录

`just up` 在父提交 `3f8a8f54e1ca511fe52dbe620ad6397e5e16b8be` 上取得 exit 0，应用、两端源码和
生产编排输入与观察提交相同；随后唯一代码变化是根单测的运行路径隔离。本次未复现历史 Docker
固定地址冲突，没有手工断开网络、删除容器或数据卷。默认中间件已启动，可选 embedding/infer 保持
停止，无关租车数据库与 net-transfer 服务保持运行。

并行启动时，旧凭据轮换测试因未覆盖默认 RUN_DIR/APP_LIFECYCLE_LOCK 而等待真实栈锁，第一次
`just test-dev` 以 1 个 TimeoutExpired 失败。修复只把该测试的所有运行路径指向 TemporaryDirectory，
没有放宽生产锁或原断言；随后在观察提交重跑全部根单测、知识与生成契约检查通过。父提交另有
52 项定向套件通过（5.38s），正式最终结果以上述观察提交的全量执行为准。

两个 fixture recipe 均恢复原 worker 并通过 readiness。最后逐项比较运行进程的 26 个
ASSISTANT_LLM/Tavily 环境值与正常加载配置，全部相同；env 文件前后 SHA-256 相同，权限保持 0600，
fixture pid 已移除。未输出或归档配置值、凭据；没有改变正常 provider、模型或 canary 门槛。

## 证据与未覆盖范围

持久结果摘要见 artifacts。原始日志位于 `/tmp/little-review-fix-validation-20260908-ka9yNl`，包含
首次失败和后续 `root-*-remediation-final.log`、`e2e-*-final.log`，未覆盖或改写失败日志。本页、结果
摘要和已入库测试共同保存可审查记录，不依赖临时日志作为唯一证明。

coverage 保留此前根联调范围并补入新测试，包含全部根运行工具、测试、契约配置与两端完整快照。
根仓不代写两端产品语义，不把本轮 17 项已确认缺陷的修复等同于所有既有产品 gap 关闭；历史 EVD、
unknown/diverged 及未验证边界保持。真实长研究与引用支持质量、人类评审、浏览器/原生设备、推荐
提升、搜索双评、容量和生产 SLO 本轮未验收；可选算法服务未启动，不据此证明向量推理链路。
