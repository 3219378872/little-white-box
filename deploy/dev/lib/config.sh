# Defaults and source-time state. Loaded first by ../stack.sh.
# shellcheck shell=bash
#
# Everything assigned with ${VAR:-default} can be overridden from the caller's
# environment. Plain assignments below are fixed facts of the backend compose
# stack or of this repo and are intentionally not overridable.

# Runtime files (pids, tokens, logs, rendered config) must stay owner-only.
umask 077

# --- Overridable paths ------------------------------------------------------
ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
BACKEND="${BACKEND:-$ROOT/little-white-box-content-community}"
FRONTEND="${FRONTEND:-$ROOT/little-white-box-front}"
KNOWLEDGE_PYTHON="${KNOWLEDGE_PYTHON:-$ROOT/.venv-knowledge/bin/python}"
RUN_DIR="${RUN_DIR:-/tmp/xbh-run}"
LOG_DIR="${LOG_DIR:-$RUN_DIR/logs}"
PID_DIR="${PID_DIR:-$RUN_DIR/pids}"
ETC_DIR="${ETC_DIR:-/tmp/xbh-etc}"
# Secrets: /tmp/xbh-dev.env wins over the git-ignored deploy/dev/.env.
ENV_FILE="${ENV_FILE:-/tmp/xbh-dev.env}"
LOCAL_ENV="${LOCAL_ENV:-$ROOT/deploy/dev/.env}"

# --- Same-origin entry proxy ------------------------------------------------
PROXY_NAME="${PROXY_NAME:-xbh-dev-proxy}"
PROXY_CONF="${PROXY_CONF:-$ROOT/deploy/dev/proxy.conf}"
PROXY_RUNTIME_CONF="${PROXY_RUNTIME_CONF:-$ETC_DIR/proxy.conf}"
# auto drops the proxy's [::] listener on Linux hosts without an IPv6 stack.
PROXY_IPV6="${PROXY_IPV6:-auto}"

# --- Middleware compose -----------------------------------------------------
OVERRIDE="${OVERRIDE:-$ROOT/deploy/dev/middleware-override.yml}"
COMPOSE_FILE="${COMPOSE_FILE:-$BACKEND/deploy/docker-compose.middleware.yml}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-deploy}"

# --- Overridable app ports and endpoints ------------------------------------
FRONT_PORT="${FRONT_PORT:-3003}"
GATEWAY_PORT="${GATEWAY_PORT:-8888}"
ENTRY_PORT="${ENTRY_PORT:-3002}"
# Derive the default URL when rendering, after environment port overrides load.
MEDIA_PUBLIC_BASE_URL="${MEDIA_PUBLIC_BASE_URL:-}"
REDIS_CONTAINER="${REDIS_CONTAINER:-xbh-redis}"
SEARCH_INDEX_URL="${SEARCH_INDEX_URL:-http://127.0.0.1:9200/xbh_posts}"

# --- Log rotation -----------------------------------------------------------
LOG_MAX_BYTES="${LOG_MAX_BYTES:-5242880}"
LOG_ROTATE_INTERVAL_SECONDS="${LOG_ROTATE_INTERVAL_SECONDS:-30}"

# --- Assistant agent and its deterministic LLM fixture ----------------------
AGENT_FIXTURE_PORT="${AGENT_FIXTURE_PORT:-39091}"
ASSISTANT_AGENT_METRICS_PORT="${ASSISTANT_AGENT_METRICS_PORT:-9136}"
ASSISTANT_AGENT_READY_TIMEOUT_SECONDS="${ASSISTANT_AGENT_READY_TIMEOUT_SECONDS:-180}"
# Log line the worker prints after its canary run; readiness waits for it.
ASSISTANT_AGENT_READY_LINE="Assistant agent worker started"

# --- Fixed facts of the backend compose stack (not overridable) -------------
# Every DSN variable a service config reads; all must use the app account.
DEV_DB_DSN_KEYS=(DB_CONTENT DB_USER DB_INTERACTION DB_MEDIA DB_MESSAGE DB_FEED DB_ASSISTANT DB_AD DB_REVIEW)
MYSQL_CONTAINER=xbh-mysql
CLICKHOUSE_CONTAINER=xbh-clickhouse
ROCKETMQ_BROKER_CONTAINER=xbh-rocketmq-broker
REDIS_PORT=6379
ETCD_PORT=2379
ELASTICSEARCH_PORT=9200
ROCKETMQ_NAMESRV_PORT=9876
ROCKETMQ_BROKER_PORT=10911
LOKI_PORT=3100
SEAWEEDFS_MASTER_PORT=9333
# Opt-in "algorithm" compose profile (just infer-up).
EMBEDDING_PORT=50051
ONLINE_INFER_PORT=9025
MODERATION_INFER_PORT=9026
# user-rpc is the first RPC every other service dials; app-up waits on it.
USER_RPC_PORT=9090
# Compose default; the env file's MYSQL_ROOT_PASSWORD wins when set.
MYSQL_ROOT_PASSWORD_DEFAULT='Xbh@MySQL2024!'
# xbh_content.post id ranges of the frozen eval corpus and the optional bulk set.
EVAL_CORPUS_IDS=1001-1300
BULK_CORPUS_IDS=2001-4000

# --- Helper scripts (exact paths double as process identity) ----------------
LOG_MAINTAINER_SCRIPT="$ROOT/deploy/dev/log_maintainer.py"
FRONT_SERVER_SCRIPT="$ROOT/deploy/dev/serve_release.py"
LLM_FIXTURE_SCRIPT="$ROOT/deploy/dev/e2e/fixtures/llm_provider.py"
# Fingerprint of the inputs that produced the served frontend bundle.
FRONT_BUILD_STAMP="$RUN_DIR/front-build.stamp"

# --- Process control timing -------------------------------------------------
# Stops poll STOP_POLLS times: TERM grace uses the longer interval, the wait
# after KILL (and cleanup of a process started moments ago) the shorter one.
STOP_POLLS=10
STOP_TERM_INTERVAL=0.2
STOP_KILL_INTERVAL=0.1
# How long a fresh launch must survive before it counts as started.
LAUNCH_SETTLE_SECONDS=0.1

# --- Source-time state (reset on every source) ------------------------------
AGENT_FIXTURE_RESTORE=0
APP_LIFECYCLE_LOCK="${APP_LIFECYCLE_LOCK:-$RUN_DIR/app-lifecycle.lock}"
APP_LIFECYCLE_LOCK_FD=""
MANAGED_PROCESS_TOKEN_ENV="XBH_STACK_PROCESS_TOKEN"
APP_UP_TRACK_STARTS=0
APP_UP_STARTED_SERVICES=()
LAUNCHED_PID=""
LAUNCHED_TOKEN=""

# Managed Go services as "name|workdir|package|flag|config". Order matters:
# app-up starts them in this order and app-down stops them in reverse.
# Configs point at ETC_DIR copies rendered by prepare_etc, never the sub-repo.
RPC_SERVICES=(
  "user-rpc|$BACKEND|./app/user/rpc|-f|$ETC_DIR/app/user/rpc/etc/user.yaml"
  "content-rpc|$BACKEND|./app/content/rpc|-f|$ETC_DIR/app/content/rpc/etc/content.yaml"
  "media-rpc|$BACKEND|./app/media/rpc|-f|$ETC_DIR/app/media/rpc/etc/media.yaml"
  "interaction-rpc|$BACKEND|./app/interaction/rpc|-f|$ETC_DIR/app/interaction/rpc/etc/interaction.yaml"
  "behavior-rpc|$BACKEND|./app/behavior/rpc|-f|$ETC_DIR/app/behavior/rpc/etc/behavior.yaml"
  "search-rpc|$BACKEND|./app/search/rpc|-f|$ETC_DIR/app/search/rpc/etc/search.yaml"
  "recommend-rpc|$BACKEND|./app/recommend/rpc|-f|$ETC_DIR/app/recommend/rpc/etc/recommend.yaml"
  "message-rpc|$BACKEND|./app/message/rpc|-f|$ETC_DIR/app/message/rpc/etc/message.yaml"
  "feed-rpc|$BACKEND|./app/feed/rpc|-f|$ETC_DIR/app/feed/rpc/etc/feed.yaml"
  "assistant-rpc|$BACKEND|./app/assistant/rpc|-f|$ETC_DIR/app/assistant/rpc/etc/assistant.yaml"
  "review-rpc|$BACKEND|./app/review/rpc|-f|$ETC_DIR/app/review/rpc/etc/review.yaml"
  "ad-rpc|$BACKEND|./app/ad/rpc|-f|$ETC_DIR/app/ad/rpc/etc/ad.yaml"
)

# Consumers and workers; app-up starts them after the RPC tier and topics.
MQ_SERVICES=(
  "search-mq|$BACKEND|./app/search/mq|-f|$ETC_DIR/app/search/mq/etc/search-consumer.yaml"
  "feed-mq|$BACKEND|./app/feed/mq|-f|$ETC_DIR/app/feed/mq/etc/feed-consumer.yaml"
  "media-mq|$BACKEND|./app/media/mq|-f|$ETC_DIR/app/media/mq/etc/media-consumer.yaml"
  "recommend-mq|$BACKEND|./app/recommend/mq|-f|$ETC_DIR/app/recommend/mq/etc/recommend-consumer.yaml"
  "behavior-log|$BACKEND|./app/pipeline/behaviorlog|-f|$ETC_DIR/app/pipeline/behaviorlog/etc/behavior-log.yaml"
  "content-cleanup|$BACKEND|./app/content/mq/cleanup|-f|$ETC_DIR/app/content/mq/cleanup/etc/content-cleanup.yaml"
  "assistant-agent|$BACKEND|./app/assistant/worker|-f|$ETC_DIR/app/assistant/worker/etc/agent.yaml"
  "review-worker|$BACKEND|./app/review/worker|-f|$ETC_DIR/app/review/worker/etc/review-worker.yaml"
  "ad-mq|$BACKEND|./app/ad/mq|-f|$ETC_DIR/app/ad/mq/etc/ad-consumer.yaml"
)

