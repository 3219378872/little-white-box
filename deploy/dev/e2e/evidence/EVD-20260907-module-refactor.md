---
id: EVD-20260907-module-refactor
status: active
result: passed
updated_at: 2026-09-07
observed_commit: 1fd2a6e8c91c5fa576b4806a298051ea6395c042
commands:
  - bash -n deploy/dev/stack.sh
  - for module in deploy/dev/lib/*.sh; do bash -n "$module"; done
  - just --list
  - just test-dev
  - just knowledge-check
  - BACKEND_GENERATE_PYTHON=/tmp/esx-embedding-proto-venv/bin/python3 just contract-check
  - just up
  - just status
  - curl --fail --silent --show-error http://127.0.0.1:3002/api/v1/health/ready
  - just e2e-agent-reset
  - just e2e-agent-research
  - just e2e
scope:
  - static
  - unit
  - integration
  - e2e
  - synthetic
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
      - deploy/dev/e2e/test_search.py
      - deploy/dev/e2e/test_user.py
      - little-white-box-content-community
      - little-white-box-front
external_upstream:
  - little-white-box-content-community@f07f5d2d137e61af2406c217756215c4f75eff16:IMP-assistant-agent
artifacts:
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/root-unit-final.log
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/root-knowledge-final.log
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/root-contract-final.log
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/e2e-agent-reset.log
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/e2e-agent-research.log
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/e2e-full.log
  - deploy/dev/e2e/evidence/assets/module-refactor-20260907/stack-status-final.log
---

# 职责拆分跨仓联调验收

本页观察根编排及两个已合并子仓 gitlink；两端版本由 observed_commit 推导。实现和单仓证明仍由
两端各自的 EVD/IMP 持有，本页只记录三仓组合后的回归结果，不修改产品条款或符合性状态。

## 已通过检查

| 检查 | 实际结果 |
| --- | --- |
| Shell 与命令入口 | 垫片和 10 个模块全部通过 bash -n；just --list 可解析 |
| 根编排单测 | 131 项通过，包含统一目录发现、模块加载与原安全/生命周期用例 |
| 知识门禁 | 根仓 46 项、后端 40+11 项通过；前端 62 文档/54 条款通过；两个 gitlink 与 HEAD 一致，10 个跨仓固定引用可解析 |
| 生成契约 | 临时 clone 重生成后端零漂移；前端两份 Gateway SDK 均 current 且逐字节相同 |
| 启动与就绪 | just up 最终 exit 0；页面 :3002 与前端 :3003 为 200，worker alive ready，健康接口 ready 且 9 项依赖 ok |
| reset/replay | 1 passed，1.33 秒；正常 worker 恢复 ready |
| 研究 fixture | 4 passed，8.25 秒；正常 worker 恢复 ready |
| 正常配置全量 E2E | 117 passed、5 skipped，171.74 秒；跳过的是上述两个 fixture recipe 专用的 1+4 项 |

source stack.sh 仍导出原 124 个函数。用 Bash declare -f 对照，除 knowledge_check 中迁移后的测试
模块路径外，124 个原函数体一致；唯一新增函数为 test_dev。默认值移至 lib/config.sh，入口固定加载
10 个模块，业务 ROOT 覆盖不影响模块定位。非 E2E 测试统一在 deploy/dev/tests/，没有保留第二份旧用例。

后端 API/proto/SQL/Go 依赖和前端 SDK/依赖锁/主题相对各自任务基线无变更。单仓细节和前端 Mock
浏览器截图分别记录在后端 EVD-20260907-module-refactor 与前端 EVD-module-refactor-2026-09-07。
输入覆盖根脚本、模块、全部测试/fixture、配置、共享工具和完整两端快照；不把新证据自身列作运行输入。

## 现场恢复与配置

合并前应用已停止并显示 stale-pid，入口为 502。首次 just up 在业务 etcd 启动前报 Address already
in use；实际占用固定地址 172.28.3.5 的是动态地址容器 xbh-milvus。临时断开它的网络后启动
xbh-etcd，再以原 xbh-milvus/milvus 别名重连；Milvus 获得 172.28.3.11，相关容器均 healthy。
之后重新运行 just up 及全部门禁成功。未删容器/数据卷、改 Compose 或启用可选算法服务；这里只是
运行态恢复，不是动态地址与固定地址冲突的持久化修复。

两次 fixture 只在子进程覆盖配置。恢复后逐项比较 worker 的 26 项 ASSISTANT_LLM/Tavily 环境值与
原环境一致，fixture endpoint/model 均未残留；env 文件前后 SHA-256 一致，没有输出或归档凭据值。

## 未覆盖边界

正常配置下的基础模型/Watch 用例本次通过，但不代表真实长研究任务的质量、任意 provider 兼容性、
人类评审、实体设备、容量或生产 SLO。前端 Mock 浏览器证明不替代真实长链路浏览器验收。本页不升级
两端原有 unknown/diverged，不改写历史失败记录，也不以结构拆分关闭尚未执行的产品门禁。
