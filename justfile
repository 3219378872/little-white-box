# 小白盒联调栈：中间件容器 + RPC/MQ + Gateway + Flutter + :3002 反代
# 密钥从 /tmp/xbh-dev.env 或 deploy/dev/.env 读取，不进仓库。

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

root := justfile_directory()
backend := root / "little-white-box-content-community"
frontend := root / "little-white-box-front"
run_dir := "/tmp/xbh-run"
etc_dir := "/tmp/xbh-etc"
env_file := "/tmp/xbh-dev.env"
local_env := root / "deploy/dev/.env"
proxy_name := "xbh-dev-proxy"
proxy_conf := root / "deploy/dev/proxy.conf"
front_port := "3003"
gateway_port := "8888"
entry_port := "3002"

default:
    @just --list

# 全量启动：中间件、覆盖配置、后端、前端、反代
up: middleware-up app-up
    @just status

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
    apply_dev_user

# 把 eval/corpus.json 与 deploy/dev/corpus_2000.json 灌入 xbh_content.post（幂等）
seed-eval-corpus:
    #!/usr/bin/env bash
    set -euo pipefail
    ROOT="{{root}}"
    # shellcheck source=/dev/null
    source "$ROOT/deploy/dev/stack.sh"
    apply_eval_corpus

# 测试账号 + eval 语料
seed: seed-dev-user seed-eval-corpus

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
