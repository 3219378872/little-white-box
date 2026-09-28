---
id: EVD-20260928-cloudwego-migration
status: active
result: passed
updated_at: '2026-09-28'
observed_commit: 630f61107426f627b348d645106d634a39b70e89
commands:
- just app-down
- just app-up
- just knowledge-check
- PATH=/tmp/little-migrate/bin:/tmp/little-migrate/tooling/bin:$PATH BACKEND_GENERATE_PYTHON=/tmp/little-migrate/tooling/bin/python
  just contract-check
- just test-dev
- just e2e
- just e2e-agent-research
- just e2e-agent-reset
- just e2e deploy/dev/e2e/test_assistant.py::test_async_message_and_event_reconnect
- /tmp/little-media-tools/bin/python deploy/dev/e2e/media_browser.py --output /tmp/little-migrate/evidence/media-browser-final
- /tmp/little-media-tools/bin/python deploy/dev/e2e/gateway_browser.py --output /tmp/little-migrate/evidence/gateway-browser-final
- just status
- curl --fail --silent --show-error --max-time 5 http://127.0.0.1:3002/api/v1/health/ready
- cmp /tmp/little-migrate/evidence/volumes-before-switch.txt /tmp/little-migrate/evidence/volumes-after-switch.txt
scope:
- static
- unit
- e2e
- browser
- synthetic
- live-provider
coverage:
- requirements:
  - little-white-box-front:FQ-002
  - little-white-box-front:FX-040
  - little-white-box-content-community:CORE-024
  - little-white-box-content-community:CORE-040
  - little-white-box-content-community:CORE-054
  paths:
  - .gitignore
  - .gitmodules
  - AGENTS.md
  - NOTES.md
  - README.md
  - deploy/dev/README.md
  - deploy/dev/e2e/api_client.py
  - deploy/dev/e2e/conftest.py
  - deploy/dev/e2e/dbprobe.py
  - deploy/dev/e2e/fixtures/README.md
  - deploy/dev/e2e/fixtures/clip.mp4
  - deploy/dev/e2e/fixtures/image.png
  - deploy/dev/e2e/fixtures/llm_provider.py
  - deploy/dev/e2e/fixtures/voice.m4a
  - deploy/dev/e2e/fixtures/voice.mp3
  - deploy/dev/e2e/fixtures/voice.wav
  - deploy/dev/e2e/gateway_browser.py
  - deploy/dev/e2e/media_browser.py
  - deploy/dev/e2e/poll.py
  - deploy/dev/e2e/pytest.ini
  - deploy/dev/e2e/requirements.txt
  - deploy/dev/e2e/support.py
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
  - deploy/dev/e2e/test_media_messages.py
  - deploy/dev/e2e/test_message.py
  - deploy/dev/e2e/test_post.py
  - deploy/dev/e2e/test_review_regressions.py
  - deploy/dev/e2e/test_search.py
  - deploy/dev/e2e/test_user.py
  - deploy/dev/lib/checks.sh
  - deploy/dev/lib/config.sh
  - deploy/dev/lib/env.sh
  - deploy/dev/lib/fixtures.sh
  - deploy/dev/lib/frontend.sh
  - deploy/dev/lib/lifecycle.sh
  - deploy/dev/lib/middleware.sh
  - deploy/dev/lib/process_control.sh
  - deploy/dev/lib/process_identity.sh
  - deploy/dev/lib/readiness.sh
  - deploy/dev/log_maintainer.py
  - deploy/dev/middleware-override.yml
  - deploy/dev/proxy.conf
  - deploy/dev/requirements-knowledge.txt
  - deploy/dev/seed_dev_user.sql
  - deploy/dev/serve_release.py
  - deploy/dev/stack.sh
  - deploy/dev/tests/__init__.py
  - deploy/dev/tests/stack_support.py
  - deploy/dev/tests/test_llm_provider.py
  - deploy/dev/tests/test_log_maintainer.py
  - deploy/dev/tests/test_stack_config.py
  - deploy/dev/tests/test_stack_credentials.py
  - deploy/dev/tests/test_stack_entrypoints.py
  - deploy/dev/tests/test_stack_fixtures.py
  - deploy/dev/tests/test_stack_frontend.py
  - deploy/dev/tests/test_stack_lifecycle.py
  - deploy/dev/tests/test_stack_loading.py
  - deploy/dev/tests/test_stack_port_cleanup.py
  - deploy/dev/tests/test_stack_process_identity.py
  - deploy/dev/tests/test_stack_process_start.py
  - deploy/dev/tests/test_stack_process_stop.py
  - deploy/dev/tests/test_stack_readiness.py
  - deploy/dev/tests/test_stack_safety.py
  - deploy/dev/tests/test_workspace_checks.py
  - deploy/dev/workspace_checks.py
  - justfile
  - little-white-box-content-community
  - little-white-box-front
artifacts:
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/gateway-assistant.png
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/gateway-browser.json
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/gateway-build-version.txt
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/gateway-content.png
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/media-1440-light.png
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/media-320-light.png
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/media-390-dark.png
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/media-browser.json
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/provider-runtime-config.json
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/readiness.json
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/receiver-history.png
- deploy/dev/e2e/evidence/assets/cloudwego-migration-20260928/validation-summary.json
---

# Kitex / Hertz 整栈迁移验收

观察根提交固定两个子仓版本；运行中的 Gateway 构建信息对应后端 gitlink，Kitex v0.16.2、Hertz
v0.10.6，vcs.modified=false。公开契约改为 OpenAPI，后端重复生成与两份 Dart SDK 字节一致。
源码、隔离依赖和 Python 流水线的后端证据见子仓 EVD-20260928-cloudwego-migration；空查询参数
修复及最终静态/race/覆盖率结果见 EVD-20260928-cloudwego-query。前端源码门禁包含 627 项 Flutter
测试、47 项工具测试和真实入口 Web release 构建，其独立证据位于前端子仓。

| 范围 | 实际结果 |
| --- | --- |
| 根知识 / 契约 / 编排测试 | gitlink 与子仓 HEAD 一致，生成零漂移，132 tests passed |
| 正常真实栈 E2E | 148 passed、5 skipped；跳过项仅要求专用 fixture |
| 确定性 fixture | 研究/问答/来源 4 passed；reset/replay 1 passed，退出后恢复正常 provider worker；真实模型生成/重连另 1 passed |
| 媒体浏览器 | 320 亮色、390 暗色、1440 亮色上传并发送图片/视频/语音；刷新、接收历史、媒体打开通过 |
| Gateway 浏览器 | 界面登录、有效 refresh token 对无效 access 签名的真实刷新、点赞收藏、Assistant 重载订阅通过 |
| 正常运行 | 同源 :3002 可用，Gateway ready 与 9 项依赖 ok，Assistant worker ready |
| 数据 | 22 个 Compose 数据卷名称前后一致，未删除卷；旧二进制/配置已备份，未执行回滚 |

正常 E2E 的 Assistant 调用走已配置的真实 GLM Responses provider，fallback 关闭。异步生成与
SSE 游标重连完成门禁通过；fixture 的五项结果只证明受控场景，不替代真实模型质量、设备、容量
或生产证明。浏览器 Assistant 截图展示重载后的持久化请求和订阅，未把加载中的截图当成生成完成。
可选 embedding/inference 侧车未启动，推荐保持既有规则降级；本次未部署生产。

首轮 E2E 的空关键词业务码回归已通过修复参数绑定解决，没有放宽断言；最终完整套件重新通过。
首次 UI 登录脚本的 DOM fill 在 Flutter 密码焦点切换时丢失输入，脚本改为聚焦后键盘输入；
最终浏览器结果来自修正后脚本的完整重跑。浏览器产物不记录 JWT/refresh token，测试帖子已删除；
平台无删除测试用户接口，专用注册账号按现有联调约定保留。
