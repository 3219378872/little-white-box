---
id: EVD-20260919-frontend-quality-fixes
status: active
result: passed
updated_at: 2026-09-19
observed_commit: db91c6fadac43679370031a3bc537d88be765076
commands:
  - bash -n deploy/dev/stack.sh
  - for module in deploy/dev/lib/*.sh; do bash -n "$module" || exit; done
  - just --list
  - KNOWLEDGE_PYTHON=/home/dev/projects/little/.venv-knowledge/bin/python just test-dev
  - BACKEND=/home/dev/projects/little/little-white-box-content-community FRONTEND=/home/dev/projects/little/little-white-box-front KNOWLEDGE_PYTHON=/home/dev/projects/little/.venv-knowledge/bin/python just knowledge-check
  - PATH=/tmp/little-quality-tools-nsA9h9/bin:/tmp/little-quality-tools-nsA9h9/protoc/bin:$PATH BACKEND_GENERATE_PYTHON=/tmp/little-quality-tools-nsA9h9/python/bin/python3 BACKEND=/home/dev/projects/little/little-white-box-content-community FRONTEND=/home/dev/projects/little/little-white-box-front KNOWLEDGE_PYTHON=/home/dev/projects/little/.venv-knowledge/bin/python just contract-check
  - PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --collect-only -q deploy/dev/e2e/test_user.py
  - just status
scope: [static, unit]
coverage:
  - requirements:
      - little-white-box-front:FQ-002
      - little-white-box-front:FQ-008
      - little-white-box-content-community:CORE-062
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
artifacts:
  - deploy/dev/e2e/evidence/assets/frontend-quality-20260919/validation-summary.json
---

# 前端质量修复跨仓契约与编排验证

本页观察根提交 `db91c6fadac43679370031a3bc537d88be765076`，两端版本由该提交的 gitlink 推导。
静态/单元命令在根 task 工作树执行，显式指向已集成的子仓主检出；`just status` 在同提交主检出执行。
证据随后独立提交，没有将子仓文件内容纳入根仓。后端并行质量修复已保留，本轮仅叠加访问者关注状态
契约、前端八项修复和资料 HTTP 回归断言。

| 验证 | 实际结果 |
| --- | --- |
| Shell 与 recipe | 入口、10 个模块语法检查通过；`just --list` 可解析 |
| 根编排单测 | 131 passed |
| 跨仓知识 | 两个 gitlink 与实际 HEAD 一致；两端门禁通过，12 条固定跨仓引用有效 |
| 后端生成 | 在一次性 clone 中执行完整 `make generate`，零漂移 |
| Flutter SDK | 从固定后端 gitlink 取 gateway.api 重生成，来源和应用副本均 current 且字节一致 |
| 用户资料 E2E 收集 | 10 项成功收集；没有执行真实 HTTP 测试 |
| 运行状态检查 | 本地联调容器、应用进程和代理均未启动，3002/3003/8888 不可用 |

`test_follow_unfollow_roundtrip` 现在检查关注前、关注后、其他访问者、匿名、本人及取消后的
`isFollowing`；公共资料形状也要求匿名返回 false。该 HTTP 回归已入库并可收集，但本页不以此声称
真实网关已验收。业务读取的隔离 MySQL 验证归后端 `EVD-20260919-profile-follow-state`，客户端
576 项回归、83.2% 手写行覆盖率和 Web 构建归前端 `EVD-quality-fixes-2026-09-19`。

coverage 保留此前跨仓证据的全部根输入与两个子仓完整快照，新增 CORE-062 仅关联契约生成和加字段
兼容性检查，不升级其子仓整体符合性。历史证据的结果和未验证 gap 均保留。生成工具安装在任务临时
目录：goctl 1.10.1、protoc 3.21.12、protoc-gen-go 1.36.11、protoc-gen-go-grpc 1.6.1，Python
使用仓库固定的 grpcio-tools 1.71.0 / protobuf 5.29.4；未更改项目依赖。

本页 passed 仅指实际运行的静态与单元检查。未启动或部署本地栈，未执行完整 HTTP E2E、浏览器、
实体设备、真实模型/provider、容量或生产验收。原始日志在 `/tmp/little-quality-tools-nsA9h9`，持久
结果摘要见 artifacts。
