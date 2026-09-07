# 小白盒联调栈：中间件容器 + RPC/MQ + Gateway + Flutter + :3002 反代
# 密钥从 /tmp/xbh-dev.env 或 deploy/dev/.env 读取，不进仓库。

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# 路径、端口、容器名的默认值集中在 deploy/dev/lib/config.sh，可用环境变量覆盖；
# 这里只保留 recipe 插值需要的根目录。
root := justfile_directory()

# 列出可用命令
default:
    @just --list

# 全量启动：先停旧应用，再迁移中间件，最后启动同一源码版本的应用
up:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    stack_up

# 全量停止：应用进程、反代、算法容器、中间件容器（保留数据卷）
down:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    stack_down

# 重启全栈
restart:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    stack_restart

# 当前容器、进程和入口探测
status:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    stack_status

# 安装三个仓库隔离且固定版本的知识工具依赖
knowledge-setup:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    knowledge_setup

# 根编排单测（不启动真实联调栈）
test-dev:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    test_dev

# 校验 gitlink、跨仓固定引用及前后端知识门禁
knowledge-check:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    knowledge_check

# 在临时 clone 中校验后端生成物，并只读核对前端 Gateway SDK
contract-check:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    contract_check

# 原子轮换 app/e2e MySQL 凭据；不改 provider/gateway 等其他配置，不输出新值
rotate-db-credentials:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    rotate_dev_db_credentials

# 写入本地测试账号 admin / 123456（幂等，已存在则重置密码）
seed-dev-user:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    load_env
    apply_dev_user

# 把后端 eval/corpus.json 与 eval/dev/corpus_2000.json 灌入 xbh_content.post（幂等）
seed-eval-corpus:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    load_env
    apply_eval_corpus

# 测试账号 + eval 语料
seed: seed-dev-user seed-eval-corpus

# 黑盒 e2e 套件（对真实联调栈；可传 pytest 路径/-k 过滤，如 just e2e -k "not slow"）
[positional-arguments]
e2e *args:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    load_env
    export PYTHONDONTWRITEBYTECODE=1
    has_selection=0
    for arg in "$@"; do
        case "$arg" in
            *.py|*.py::*|*::*|*/e2e|*/e2e/) has_selection=1 ;;
        esac
    done
    if [[ "$has_selection" -eq 0 ]]; then
        set -- "$@" "$ROOT/deploy/dev/e2e"
    fi
    exec python3 -m pytest -v "$@"

# 确定性验证 provider 截断重试、response_reset、SSE replay，并恢复原 agent provider
e2e-agent-reset:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    e2e_agent_reset

# 结构化问答、社区/外部来源与原子回答发布的确定性真实栈门禁
e2e-agent-research:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    e2e_agent_research

# 只起/停 Docker 中间件
middleware-up:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    middleware_up

# 只停 Docker 中间件（保留数据卷）
middleware-down:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    middleware_down

# 可选算法服务（embedding + 在线推理；首次启动需下载模型权重）
infer-up:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    algorithm_up

# 停可选算法服务（embedding + 在线推理；容器只停不删，保留数据卷）
infer-down:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    algorithm_down

# 只起/停本机应用（RPC/MQ/Gateway/Flutter/反代）
app-up:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    app_up

# 只停本机应用（RPC/MQ/Gateway/Flutter/反代）
app-down:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    app_down

alias start := up
alias stop := down
