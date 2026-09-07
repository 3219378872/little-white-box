# Defaults and source-time state. Loaded first by ../stack.sh.
# shellcheck shell=bash

umask 077

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
BACKEND="${BACKEND:-$ROOT/little-white-box-content-community}"
FRONTEND="${FRONTEND:-$ROOT/little-white-box-front}"
KNOWLEDGE_PYTHON="${KNOWLEDGE_PYTHON:-$ROOT/.venv-knowledge/bin/python}"
RUN_DIR="${RUN_DIR:-/tmp/xbh-run}"
LOG_DIR="${LOG_DIR:-$RUN_DIR/logs}"
PID_DIR="${PID_DIR:-$RUN_DIR/pids}"
ETC_DIR="${ETC_DIR:-/tmp/xbh-etc}"
ENV_FILE="${ENV_FILE:-/tmp/xbh-dev.env}"
LOCAL_ENV="${LOCAL_ENV:-$ROOT/deploy/dev/.env}"
PROXY_NAME="${PROXY_NAME:-xbh-dev-proxy}"
CANVASKIT_DIR="${CANVASKIT_DIR:-}"
PROXY_CONF="${PROXY_CONF:-$ROOT/deploy/dev/proxy.conf}"
OVERRIDE="${OVERRIDE:-$ROOT/deploy/dev/middleware-override.yml}"
COMPOSE_FILE="${COMPOSE_FILE:-$BACKEND/deploy/docker-compose.middleware.yml}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-deploy}"
FRONT_PORT="${FRONT_PORT:-3003}"
GATEWAY_PORT="${GATEWAY_PORT:-8888}"
ENTRY_PORT="${ENTRY_PORT:-3002}"
REDIS_CONTAINER="${REDIS_CONTAINER:-xbh-redis}"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-5242880}"
LOG_ROTATE_INTERVAL_SECONDS="${LOG_ROTATE_INTERVAL_SECONDS:-30}"
AGENT_FIXTURE_PORT="${AGENT_FIXTURE_PORT:-39091}"
ASSISTANT_AGENT_METRICS_PORT="${ASSISTANT_AGENT_METRICS_PORT:-9136}"
ASSISTANT_AGENT_READY_TIMEOUT_SECONDS="${ASSISTANT_AGENT_READY_TIMEOUT_SECONDS:-180}"
ASSISTANT_AGENT_READY_LINE="Assistant agent worker started"
AGENT_FIXTURE_RESTORE=0
APP_LIFECYCLE_LOCK="${APP_LIFECYCLE_LOCK:-$RUN_DIR/app-lifecycle.lock}"
APP_LIFECYCLE_LOCK_FD=""
MANAGED_PROCESS_TOKEN_ENV="XBH_STACK_PROCESS_TOKEN"
APP_UP_TRACK_STARTS=0
APP_UP_STARTED_SERVICES=()

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
)

MQ_SERVICES=(
  "search-mq|$BACKEND|./app/search/mq|-f|$ETC_DIR/app/search/mq/etc/search-consumer.yaml"
  "feed-mq|$BACKEND|./app/feed/mq|-f|$ETC_DIR/app/feed/mq/etc/feed-consumer.yaml"
  "media-mq|$BACKEND|./app/media/mq|-f|$ETC_DIR/app/media/mq/etc/media-consumer.yaml"
  "recommend-mq|$BACKEND|./app/recommend/mq|-f|$ETC_DIR/app/recommend/mq/etc/recommend-consumer.yaml"
  "behavior-log|$BACKEND|./app/pipeline/behaviorlog|-f|$ETC_DIR/app/pipeline/behaviorlog/etc/behavior-log.yaml"
  "content-cleanup|$BACKEND|./app/content/mq/cleanup|-f|$ETC_DIR/app/content/mq/cleanup/etc/content-cleanup.yaml"
  "assistant-watch|$BACKEND|./app/assistant/mq|-f|$ETC_DIR/app/assistant/mq/etc/watch-consumer.yaml"
  "assistant-agent|$BACKEND|./app/assistant/worker|-f|$ETC_DIR/app/assistant/worker/etc/agent.yaml"
)

