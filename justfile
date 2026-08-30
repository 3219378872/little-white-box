# 小白盒联调栈：中间件容器 + RPC/MQ + Gateway + Flutter + :3002 反代
# 密钥从 /tmp/xbh-dev.env 或 deploy/dev/.env 读取，不进仓库。

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# 路径、端口、容器名的默认值集中在 stack.sh 文件头，可用环境变量覆盖；
# 这里只保留 recipe 插值需要的根目录。
root := justfile_directory()

default:
    @just --list

# 全量启动：先停旧应用，再迁移中间件，最后启动同一源码版本的应用
up:
    just app-down
    just middleware-up
    just app-up
    just status

# 全量停止：应用进程、反代、中间件容器（保留数据卷）
down: app-down middleware-down
    @echo "stopped"

# 重启全栈
restart:
    just down
    just up

# 当前容器、进程和入口探测
status:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    stack_status

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

# 黑盒 e2e 套件（对真实联调栈；可传 pytest 路径/-k 过滤，如 just e2e -k search）
e2e *args="":
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    load_env >/dev/null 2>&1 || true
    export PYTHONDONTWRITEBYTECODE=1
    set -- {{args}}
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

# 只起/停 Docker 中间件
middleware-up:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    middleware_up

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

app-down:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    app_down

alias start := up
alias stop := down
