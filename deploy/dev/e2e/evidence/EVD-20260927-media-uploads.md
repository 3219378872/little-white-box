---
id: EVD-20260927-media-uploads
status: active
result: passed
updated_at: '2026-09-27'
observed_commit: 82038b10d3fa9890866c2c95731bf417df88a0fd
commands:
- bash -n deploy/dev/stack.sh
- for module in deploy/dev/lib/*.sh; do bash -n "$module"; done
- just --list
- KNOWLEDGE_PYTHON=/home/dev/projects/little/.venv-knowledge/bin/python just test-dev
- KNOWLEDGE_PYTHON=/home/dev/projects/little/.venv-knowledge/bin/python just knowledge-check
- PATH=/tmp/little-quality-tools-nsA9h9/bin:/tmp/little-quality-tools-nsA9h9/protoc/bin:$PATH
  BACKEND_GENERATE_PYTHON=/tmp/little-quality-tools-nsA9h9/python/bin/python KNOWLEDGE_PYTHON=/home/dev/projects/little/.venv-knowledge/bin/python
  just contract-check
- just e2e deploy/dev/e2e/test_media.py deploy/dev/e2e/test_media_messages.py deploy/dev/e2e/test_message.py
  deploy/dev/e2e/test_health.py
- /tmp/little-media-tools/bin/python deploy/dev/e2e/media_browser.py --output /tmp/little-media-browser-final
- just status
- curl --fail --silent http://127.0.0.1:3002/api/v1/health/ready
- docker network inspect deploy_xbh-network --format '{{json .IPAM.Config}}'
- cmp /tmp/little-media-volumes-before.txt /tmp/little-media-volumes-after.txt
scope:
- static
- unit
- e2e
- browser
coverage:
- requirements:
  - little-white-box-front:FX-040
  - little-white-box-front:FX-041
  - little-white-box-front:FQ-002
  - little-white-box-front:FQ-008
  - little-white-box-content-community:CORE-024
  - little-white-box-content-community:CORE-040
  - little-white-box-content-community:CORE-041
  - little-white-box-content-community:CORE-042
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
  - deploy/dev/e2e/test_media_messages.py
  - deploy/dev/e2e/media_browser.py
artifacts:
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/media-1440-light.png
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/media-320-light.png
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/media-390-dark.png
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/model-probe-summary.json
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/network-migration.json
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/receiver-history.png
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/report.json
- deploy/dev/e2e/evidence/assets/media-uploads-20260927/validation-summary.json
---

# 视频、音频上传跨仓交付验证

观察根提交 `82038b1`，后端与前端版本由其 gitlink 推导。先整合子仓实现和独立证据提交，再在该根提交
复跑本页命令，最后独立提交本记录。子仓业务证据分别为 `EVD-20260927-media-upload-delivery` 与
`EVD-media-uploads-2026-09-27`；本页不替代子仓实现映射。

| 验证 | 结果 |
| --- | --- |
| Shell、recipe、根单元 | 语法通过，132 tests passed |
| 生成契约 | 后端一次性检出生成无漂移，两份 Flutter SDK 字节一致 |
| 知识与版本 | gitlink 与两端 HEAD 一致，子仓门禁及 12 条跨仓引用通过 |
| HTTP E2E | 媒体、私信、健康四个文件共 31 passed（3.11s） |
| 真实浏览器 | 320 亮色、390 暗色、1440 亮色上传/发送图片、视频、音频；刷新与接收方历史、媒体链接打开通过 |
| 正常本地栈 | :3002 为 200，ready 接口为 ready、9 项依赖 ok，Assistant worker alive ready |

媒体 API 使用认证 multipart 上传，保留稳定上传幂等键；图片/音频 10 MiB、视频 100 MiB，额外 multipart
开销独立限流。E2E 包含超过旧 20 MiB 代理阈值的视频、音频上限、容器轨道与类型不符、重复键重放和冲突、
他人媒体引用拒绝及权威 URL 落库。Gateway 读取有界，代理上传 body/timeout 与其匹配。Web 使用 blob 文件读取，
公开媒体通过入口同源 `/xbh-media/` 可获取；API Bearer 不附加给 S3 公共读取。

首轮最终 E2E 有 15 passed、11 failed、5 errors，用户服务日志明确记录 `snowflake clock moved backwards`，
随后注册请求出现熔断拒绝。未改变时间或 ID 策略，同一提交单独复跑 31 项与浏览器后通过；保留这一环境限制，
不把失败改写为从未发生。根静态检查与契约生成均已通过。

## Compose 默认地址

后端 Compose 默认网段改为 `172.30.240.0/24`、动态池 `172.30.240.128/25`、网关 `172.30.240.1`，
分别可由 `XBH_NETWORK_SUBNET`、`XBH_NETWORK_IP_RANGE`、`XBH_NETWORK_GATEWAY` 覆盖。
现场旧 etcd 容器仍保留历史固定地址，曾与动态容器冲突；已通过不带 `-v` 的 Compose down/up 重建项目网络。
最终 Docker inspect 确认新 IPAM，迁移前后保存的卷名清单一致。该检查证明卷未被删除，不是逐字节数据校验。

## 模型端点补充探测

按用户要求，在本次任务早期对当前 `https://api.weblearning.fun/v1` 进行小样本实际调用。模型列表仅返回
GLM 与 DeepSeek 两项；短答样本 DeepSeek 约 1.44s，GLM 约 1.73s（另一次 2.23s），不是性能基准。
DeepSeek 在当前 Responses 强制工具模式返回 HTTP 400：`Thinking mode does not support this tool_choice`；
显式 reasoning none 仍失败，Chat Completions 关闭 thinking 的请求又被端点工具选择格式校验拒绝。
这些结果不能推广为 DeepSeek 普遍不支持工具；当前接入方式下不能直接替换，因此保留 GLM、端点及凭据。

GLM 旧就绪探针每轮 64/32 输出预算包含推理 token，实测首轮需 65 token。后端将两轮预算调为 256，
继续要求强制工具、精确 nonce、有效回传与非空确认，保留失败闭锁；真实 worker 已通过 readiness。
模型探测摘要独立保存，本页 passed 不涵盖模型质量、持续延迟或长链 Agent 工作流。

## 验证边界

本页浏览器覆盖真实私信与媒体链路，不是全站或历史七场景视觉重构复验。客户端为音频文件选择、系统 URL
打开视频/音频；不新增录音、内嵌播放器或转码保证。前端另有 626 项测试、83.9% 覆盖率与 Android x64
调试包构建；无可用设备/KVM 权限，设备运行仍未验证。embedding/online-infer 未启动，本轮媒体/私信用例
不依赖它们。未做容量、生产及长链真实模型质量验收。

coverage 保留先前跨仓检查完整根输入与两个子仓快照，并加入本轮媒体 E2E/浏览器脚本，避免缩小输入获得
虚假新鲜度。原始临时日志在 `/tmp/little-media-final-*`；持久摘要、网络对照与截图见 artifacts。
