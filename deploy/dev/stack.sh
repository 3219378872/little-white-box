# Shared helpers for the workspace justfile. Sourced, not executed.
# shellcheck shell=bash

umask 077

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BACKEND="${BACKEND:-$ROOT/little-white-box-content-community}"
FRONTEND="${FRONTEND:-$ROOT/little-white-box-front}"
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

compose() {
  MINIO_ROOT_USER="${MINIO_ROOT_USER:-admin}" \
  MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-Xbh@Minio2024!}" \
  docker compose -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_FILE" \
    -f "$OVERRIDE" \
    --project-directory "$BACKEND/deploy" \
    "$@"
}

normalize_assistant_agent_metrics_port() {
  local port="${ASSISTANT_AGENT_METRICS_PORT:-}"
  if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] ||
    ((10#$port < 1 || 10#$port > 65535)); then
    echo "invalid assistant-agent metrics port: $port" >&2
    return 1
  fi
  ASSISTANT_AGENT_METRICS_PORT="$((10#$port))"
}

assistant_agent_metrics_config_matches() {
  local file="$1" port="$2"
  awk -v expected_port="$port" '
    /^Prometheus:$/ { inside = 1; next }
    inside && /^[^[:space:]]/ { inside = 0 }
    inside && $1 == "Port:" && $2 == expected_port { port_matches = 1 }
    END { exit !port_matches }
  ' "$file"
}

load_env() {
  local file="" env_status
  if [[ -f "$ENV_FILE" ]]; then
    file="$ENV_FILE"
  elif [[ -f "$LOCAL_ENV" ]]; then
    file="$LOCAL_ENV"
  else
    echo "missing env file: $ENV_FILE or $LOCAL_ENV" >&2
    return 1
  fi
  chmod 600 "$file" || return $?
  set -a
  # shellcheck disable=SC1090
  if source "$file"; then
    env_status=0
  else
    env_status=$?
  fi
  set +a
  [[ "$env_status" -eq 0 ]] || return "$env_status"
  normalize_assistant_agent_metrics_port || return $?
  export ASSISTANT_LLM_MODEL_SMALL="${ASSISTANT_LLM_MODEL_SMALL:-}"
  export TAVILY_ENDPOINT="${TAVILY_ENDPOINT:-}"
  export ASSISTANT_LLM_REVIEW_MODEL="${ASSISTANT_LLM_REVIEW_MODEL:-}"
  export ASSISTANT_LLM_CACHE_READ_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_CACHE_READ_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_CACHE_WRITE_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_CACHE_WRITE_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_REASONING_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_REASONING_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_FALLBACK_ENABLED="${ASSISTANT_LLM_FALLBACK_ENABLED:-false}"
  export ASSISTANT_LLM_FALLBACK_ROUTE_ID="${ASSISTANT_LLM_FALLBACK_ROUTE_ID:-fallback}"
  export ASSISTANT_LLM_FALLBACK_BOUNDARY="${ASSISTANT_LLM_FALLBACK_BOUNDARY:-default}"
  export ASSISTANT_LLM_FALLBACK_WIRE_API="${ASSISTANT_LLM_FALLBACK_WIRE_API:-responses}"
  export ASSISTANT_LLM_FALLBACK_ENDPOINT="${ASSISTANT_LLM_FALLBACK_ENDPOINT:-}"
  export ASSISTANT_LLM_FALLBACK_API_KEY="${ASSISTANT_LLM_FALLBACK_API_KEY:-}"
  export ASSISTANT_LLM_FALLBACK_MODEL="${ASSISTANT_LLM_FALLBACK_MODEL:-}"
  export ASSISTANT_LLM_FALLBACK_PROMPT_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_FALLBACK_PROMPT_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_FALLBACK_COMPLETION_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_FALLBACK_COMPLETION_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_FALLBACK_CACHE_READ_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_FALLBACK_CACHE_READ_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_FALLBACK_CACHE_WRITE_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_FALLBACK_CACHE_WRITE_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_FALLBACK_REASONING_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_FALLBACK_REASONING_COST_PER_MILLION_TOKENS:-0}"
  ensure_assistant_db_env || return $?
  validate_dev_db_env
}

secure_runtime_paths() {
  install -d -m 700 "$RUN_DIR" "$LOG_DIR" "$PID_DIR" "$RUN_DIR/bin" "$ETC_DIR" || return $?
  find "$LOG_DIR" -maxdepth 1 -type f -name '*.log*' -exec chmod 600 {} + 2>/dev/null || true
  find "$PID_DIR" -maxdepth 1 -type f -name '*.pid' -exec chmod 600 {} + 2>/dev/null || true
}

with_app_lifecycle_lock() {
  local mode="$1" callback="$2" lock_fd status unlock_status=0
  local previous_lock_fd="${APP_LIFECYCLE_LOCK_FD:-}"
  shift 2
  secure_runtime_paths || return $?
  command -v flock >/dev/null 2>&1 || {
    echo "flock is required for application lifecycle operations" >&2
    return 1
  }
  exec {lock_fd}>"$APP_LIFECYCLE_LOCK" || return $?
  chmod 600 "$APP_LIFECYCLE_LOCK" || {
    status=$?
    exec {lock_fd}>&-
    return "$status"
  }
  if [[ "$mode" == "shared" ]]; then
    flock -s "$lock_fd" || {
      status=$?
      exec {lock_fd}>&-
      return "$status"
    }
  else
    flock -x "$lock_fd" || {
      status=$?
      exec {lock_fd}>&-
      return "$status"
    }
  fi

  APP_LIFECYCLE_LOCK_FD="$lock_fd"
  if "$callback" "$@"; then
    status=0
  else
    status=$?
  fi
  APP_LIFECYCLE_LOCK_FD="$previous_lock_fd"
  flock -u "$lock_fd" || unlock_status=$?
  exec {lock_fd}>&-
  if [[ "$status" -ne 0 ]]; then
    return "$status"
  fi
  return "$unlock_status"
}

close_app_lifecycle_lock_fd() {
  [[ -n "${APP_LIFECYCLE_LOCK_FD:-}" ]] || return 0
  [[ "$APP_LIFECYCLE_LOCK_FD" =~ ^[0-9]+$ ]] || {
    echo "invalid application lifecycle lock fd: $APP_LIFECYCLE_LOCK_FD" >&2
    return 1
  }
  exec {APP_LIFECYCLE_LOCK_FD}>&-
  APP_LIFECYCLE_LOCK_FD=""
}

clear_sensitive_assistant_logs() {
  local name logfile
  for name in assistant-rpc assistant-watch assistant-agent; do
    logfile="$LOG_DIR/$name.log"
    if [[ -e "$logfile" && ! -L "$logfile" ]]; then
      : >"$logfile" || return $?
      chmod 600 "$logfile" || return $?
    fi
    rm -f "$logfile.1.gz" || return $?
  done
}

# assistant.yaml DataSource is "${DB_ASSISTANT}". Older env files only set
# DB_CONTENT; derive the DSN by swapping the schema name, keep user/query.
ensure_assistant_db_env() {
  if [[ -z "${DB_ASSISTANT:-}" && -n "${DB_CONTENT:-}" ]]; then
    export DB_ASSISTANT="${DB_CONTENT/xbh_content/xbh_assistant}"
  fi
}

validate_mysql_account_name() {
  local name="$1" label="$2"
  if [[ ! "$name" =~ ^[A-Za-z0-9_]{1,32}$ ]]; then
    echo "$label must match [A-Za-z0-9_]{1,32}" >&2
    return 1
  fi
  if [[ "$name" == "root" || "$name" == "xbh" ]]; then
    echo "$label must not use a reserved legacy or root account" >&2
    return 1
  fi
}

validate_dev_db_env() {
  local app_user="${APP_MYSQL_USER:-}" app_pass="${APP_MYSQL_PASSWORD:-}"
  local e2e_user="${E2E_MYSQL_USER:-}" e2e_pass="${E2E_MYSQL_PASSWORD:-}"
  validate_mysql_account_name "$app_user" APP_MYSQL_USER || return 1
  validate_mysql_account_name "$e2e_user" E2E_MYSQL_USER || return 1
  if [[ "$app_user" == "$e2e_user" ]]; then
    echo "APP_MYSQL_USER and E2E_MYSQL_USER must be different accounts" >&2
    return 1
  fi
  if [[ ${#app_pass} -lt 32 || ${#e2e_pass} -lt 32 ]]; then
    echo "APP_MYSQL_PASSWORD and E2E_MYSQL_PASSWORD must each contain at least 32 characters" >&2
    return 1
  fi
  if [[ "$app_pass" == "$e2e_pass" ]]; then
    echo "APP_MYSQL_PASSWORD and E2E_MYSQL_PASSWORD must be different" >&2
    return 1
  fi

  local key value expected_prefix="${app_user}:${app_pass}@tcp("
  for key in DB_CONTENT DB_USER DB_INTERACTION DB_MEDIA DB_MESSAGE DB_FEED DB_ASSISTANT; do
    value="${!key:-}"
    if [[ -z "$value" || "$value" != "$expected_prefix"* ]]; then
      echo "$key must use APP_MYSQL_USER/APP_MYSQL_PASSWORD over a tcp DSN" >&2
      return 1
    fi
  done
}

random_hex_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
    return
  fi
  od -An -N24 -v -tx1 /dev/urandom | tr -d '[:space:]'
}

# Atomically rotate only the local app/e2e MySQL credentials. Provider keys,
# gateways and all non-DB settings are copied byte-for-byte. DB DSN hosts,
# schemas and query strings are preserved while credentials become references
# to the new app variables. Values are never printed.
rotate_dev_db_credentials_locked() {
  local file=""
  if [[ -f "$ENV_FILE" ]]; then
    file="$ENV_FILE"
  elif [[ -f "$LOCAL_ENV" ]]; then
    file="$LOCAL_ENV"
  else
    echo "missing env file: $ENV_FILE or $LOCAL_ENV" >&2
    return 1
  fi
  if [[ -L "$file" || ! -f "$file" ]]; then
    echo "dev env must be a regular non-symlink file" >&2
    return 1
  fi
  chmod 600 "$file" || return $?

  local app_user="xbh_app" e2e_user="xbh_e2e" app_pass e2e_pass tmp
  app_pass="$(random_hex_secret)" || return 1
  e2e_pass="$(random_hex_secret)" || return 1
  if [[ ! "$app_pass" =~ ^[0-9a-f]{48}$ || ! "$e2e_pass" =~ ^[0-9a-f]{48}$ || "$app_pass" == "$e2e_pass" ]]; then
    echo "failed to generate independent MySQL credentials" >&2
    return 1
  fi

  tmp="$(mktemp "$(dirname "$file")/.xbh-dev-env.XXXXXX")" || return $?
  chmod 600 "$tmp" || {
    local chmod_status=$?
    rm -f "$tmp"
    return "$chmod_status"
  }
  local write_status
  if (
    local line key value quote dsn_tail saw_content=0
    printf 'APP_MYSQL_USER=%s\n' "$app_user" || exit $?
    printf 'APP_MYSQL_PASSWORD=%s\n' "$app_pass" || exit $?
    printf 'E2E_MYSQL_USER=%s\n' "$e2e_user" || exit $?
    printf 'E2E_MYSQL_PASSWORD=%s\n' "$e2e_pass" || exit $?
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(APP_MYSQL_USER|APP_MYSQL_PASSWORD|E2E_MYSQL_USER|E2E_MYSQL_PASSWORD)[[:space:]]*= ]]; then
        continue
      fi
      if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(DB_CONTENT|DB_USER|DB_INTERACTION|DB_MEDIA|DB_MESSAGE|DB_FEED|DB_ASSISTANT)[[:space:]]*= ]]; then
        key="${BASH_REMATCH[2]}"
        value="${line#*=}"
        value="${value#"${value%%[![:space:]]*}"}"
        quote=""
        if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
          quote="${value:0:1}"
          if [[ "${value: -1}" != "$quote" ]]; then
            echo "$key has an unterminated quoted DSN" >&2
            exit 1
          fi
          value="${value:1:${#value}-2}"
        fi
        if [[ "$value" != *"@tcp("* || "$value" != *")/"* ]]; then
          echo "$key is not a supported tcp MySQL DSN" >&2
          exit 1
        fi
        dsn_tail="tcp(${value##*@tcp(}"
        printf '%s="${APP_MYSQL_USER}:${APP_MYSQL_PASSWORD}@%s"\n' "$key" "$dsn_tail" || exit $?
        if [[ "$key" == "DB_CONTENT" ]]; then
          saw_content=1
        fi
        continue
      fi
      printf '%s\n' "$line" || exit $?
    done <"$file" || exit $?
    if [[ "$saw_content" != "1" ]]; then
      echo "DB_CONTENT is required before rotating MySQL credentials" >&2
      exit 1
    fi
  ) >"$tmp"; then
    :
  else
    write_status=$?
    rm -f "$tmp" 2>/dev/null || true
    return "$write_status"
  fi
  mv -f "$tmp" "$file" || return $?
  chmod 600 "$file" || return $?
  echo "rotated local app/e2e MySQL credentials in $file"
}

rotate_dev_db_credentials() {
  with_app_lifecycle_lock exclusive rotate_dev_db_credentials_locked
}

# Old sync Assistant stored Redis sessions under assistant:v2*. The v3 runtime
# only uses Redis for run-event notify keys, so wiping the legacy namespace on
# every app-up is idempotent and does not touch the MySQL marker.
redis_command() {
  local endpoint host port pass
  endpoint="${REDIS_HOST:-127.0.0.1:6379}"
  endpoint="${endpoint%%,*}"
  endpoint="${endpoint#redis://}"
  endpoint="${endpoint#rediss://}"
  endpoint="${endpoint%%/*}"
  host="${endpoint%:*}"
  port="${endpoint##*:}"
  if [[ "$host" == "$endpoint" ]]; then
    port=6379
  fi
  pass="${REDIS_PASSWORD:-}"

  if command -v redis-cli >/dev/null 2>&1; then
    REDISCLI_AUTH="$pass" redis-cli --no-auth-warning -h "$host" -p "$port" "$@"
    return
  fi
  if ! docker inspect "$REDIS_CONTAINER" >/dev/null 2>&1; then
    echo "redis-cli is unavailable and container $REDIS_CONTAINER is not running" >&2
    return 1
  fi
  if [[ "$host" == "127.0.0.1" || "$host" == "localhost" ]]; then
    host=127.0.0.1
  fi
  REDISCLI_AUTH="$pass" docker exec -i -e REDISCLI_AUTH "$REDIS_CONTAINER" \
    redis-cli --no-auth-warning -h "$host" -p "$port" "$@"
}

wipe_legacy_assistant_redis() {
  local script count_script deleted remaining
  script="local c='0' local n=0 repeat local r=redis.call('SCAN',c,'MATCH',ARGV[1],'COUNT',500) c=r[1] local k=r[2] if #k>0 then n=n+redis.call('UNLINK',unpack(k)) end until c=='0' return n"
  count_script="local c='0' local n=0 repeat local r=redis.call('SCAN',c,'MATCH',ARGV[1],'COUNT',500) c=r[1] n=n+#r[2] until c=='0' return n"
  echo "wiping legacy assistant redis namespace assistant:v2*"
  deleted="$(redis_command --raw EVAL "$script" 0 'assistant:v2*')" || {
    echo "legacy assistant redis wipe failed" >&2
    return 1
  }
  remaining="$(redis_command --raw EVAL "$count_script" 0 'assistant:v2*')" || {
    echo "legacy assistant redis wipe verification failed" >&2
    return 1
  }
  if [[ ! "$deleted" =~ ^[0-9]+$ || "$remaining" != "0" ]]; then
    echo "legacy assistant redis wipe incomplete (deleted=$deleted remaining=$remaining)" >&2
    return 1
  fi
  echo "legacy assistant redis keys removed: $deleted"
}

prepare_etc() {
  normalize_assistant_agent_metrics_port || return $?
  mkdir -p "$ETC_DIR" || return $?
  (
    cd "$BACKEND" || exit $?
    find app -path '*/etc/*.yaml' -print0
  ) | while IFS= read -r -d '' rel; do
    local -a sed_args=(
      -e 's/ListenOn: 0\.0\.0\.0:/ListenOn: 127.0.0.1:/'
      -e '/^DevServer:/,/^[^ ]/{s/^\([[:space:]]*\)Host: 0\.0\.0\.0/\1Host: 127.0.0.1/}'
      -e '/^RestConf:/,/^[^ ]/{s/^\([[:space:]]*\)Host: 0\.0\.0\.0/\1Host: 127.0.0.1/}'
    )
    mkdir -p "$ETC_DIR/$(dirname "$rel")" || exit $?
    # Dev copies bind everything to loopback: RPC ListenOn (etcd registration
    # must stay reachable from the host gateway), the gateway REST listener
    # (RestConf), and the DevServer diagnostics endpoints. The agent
    # Prometheus port is configurable here and in readiness/status as one
    # value. Sub-repo yaml files are never modified.
    if [[ "$rel" == "app/assistant/worker/etc/agent.yaml" ]]; then
      sed_args+=(
        -e "/^Prometheus:/,/^[^ ]/{s/^\\([[:space:]]*\\)Port: [0-9][0-9]*/\\1Port: $ASSISTANT_AGENT_METRICS_PORT/}"
      )
    fi
    sed "${sed_args[@]}" "$BACKEND/$rel" >"$ETC_DIR/$rel" || exit $?
    if [[ "$rel" == "app/assistant/worker/etc/agent.yaml" ]] &&
      ! assistant_agent_metrics_config_matches \
        "$ETC_DIR/$rel" "$ASSISTANT_AGENT_METRICS_PORT"; then
      echo "failed to configure assistant-agent Prometheus endpoint" >&2
      exit 1
    fi
  done
}

# middleware-override.yml uses the Compose Spec `ports: !override` YAML tag,
# which needs docker compose >= 2.24; older versions fail to parse or merge
# ports unexpectedly.
require_compose_version() {
  local ver min="2.24.0"
  ver="$(docker compose version --short 2>/dev/null || true)"
  if [[ -z "$ver" ]]; then
    echo "docker compose plugin not found" >&2
    return 1
  fi
  if [[ "$(printf '%s\n%s\n' "$min" "${ver#v}" | sort -V | head -n1)" != "$min" ]]; then
    echo "docker compose >= $min required (ports: !override in middleware-override.yml), got ${ver}" >&2
    return 1
  fi
}

port_open() {
  local host="$1" port="$2"
  bash -c "echo >/dev/tcp/${host}/${port}" >/dev/null 2>&1
}

wait_port() {
  local host="$1" port="$2" seconds="${3:-180}" label="${4:-$host:$port}"
  local i
  for ((i = 0; i < seconds; i++)); do
    if port_open "$host" "$port"; then
      echo "ready: $label"
      return 0
    fi
    sleep 1
  done
  echo "timeout waiting for $label" >&2
  return 1
}

wait_http() {
  local url="$1" seconds="${2:-90}" label="${3:-$url}"
  local i code
  for ((i = 0; i < seconds; i++)); do
    code="$(http_code "$url")"
    if [[ "$code" == "200" ]]; then
      echo "ready: $label"
      return 0
    fi
    sleep 1
  done
  echo "timeout waiting for $label" >&2
  return 1
}

# Single capture: curl failures (timeout, refused) must yield exactly "000",
# not "000\n000" from a fallback echo racing with -w output.
http_code() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null || true)"
  [[ -n "$code" ]] || code="000"
  printf '%s' "$code"
}

is_managed_binary_service() {
  local wanted="$1" row name
  [[ "$wanted" == "gateway" ]] && return 0
  for row in "${RPC_SERVICES[@]}" "${MQ_SERVICES[@]}"; do
    IFS='|' read -r name _ <<<"$row"
    [[ "$name" == "$wanted" ]] && return 0
  done
  return 1
}

# A managed Go process executes its private runtime binary. Python helpers are
# identified by an exact script argument because /proc/<pid>/exe is Python.
service_identity() {
  local name="$1"
  case "$name" in
    frontend)
      printf 'arg|%s\n' "$ROOT/deploy/dev/serve_release.py"
      ;;
    log-maintainer)
      printf 'arg|%s\n' "$ROOT/deploy/dev/log_maintainer.py"
      ;;
    llm-fixture)
      printf 'arg|%s\n' "$ROOT/deploy/dev/e2e/fixtures/llm_provider.py"
      ;;
    *)
      is_managed_binary_service "$name" || return 1
      printf 'exe|%s\n' "$RUN_DIR/bin/$name"
      ;;
  esac
}

safe_pid() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  ((10#$pid > 1)) || return 1
  [[ "$pid" != "$$" && "$pid" != "$BASHPID" ]]
}

canonical_path() {
  local path="$1" resolved
  if resolved="$(readlink -f "$path" 2>/dev/null)" && [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  (
    cd "$(dirname "$path")" 2>/dev/null || exit $?
    printf '%s/%s\n' "$(pwd -P)" "$(basename "$path")"
  )
}

process_executable_matches() {
  local pid="$1" expected="$2" actual="" command=""
  expected="$(canonical_path "$expected")" || return 1
  if [[ -L "/proc/$pid/exe" ]]; then
    actual="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    actual="${actual% (deleted)}"
    [[ "$actual" == "$expected" ]]
    return
  fi

  # Non-Linux fallback. Managed paths must not contain whitespace here because
  # portable ps exposes a flattened command line rather than argv boundaries.
  command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  actual="${command%%[[:space:]]*}"
  [[ -n "$actual" ]] || return 1
  actual="$(canonical_path "$actual")" || return 1
  [[ "$actual" == "$expected" ]]
}

process_has_exact_arg() {
  local pid="$1" expected="$2" arg command
  if [[ -r "/proc/$pid/cmdline" ]]; then
    while IFS= read -r -d '' arg; do
      [[ "$arg" == "$expected" ]] && return 0
    done <"/proc/$pid/cmdline"
    return 1
  fi

  # Best-effort fallback for systems without procfs; workspace paths are
  # required to be whitespace-free for an unambiguous flattened ps match.
  command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ " $command " == *" $expected "* ]]
}

process_executable_is_python() {
  local pid="$1" actual="" command=""
  if [[ -L "/proc/$pid/exe" ]]; then
    actual="$(readlink "/proc/$pid/exe" 2>/dev/null || true)"
    actual="${actual% (deleted)}"
  else
    command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    actual="${command%%[[:space:]]*}"
  fi
  actual="${actual##*/}"
  [[ "$actual" == python || "$actual" == python[0-9]* ]]
}

service_process_matches() {
  local name="$1" pid="$2" identity kind expected
  safe_pid "$pid" || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  identity="$(service_identity "$name")" || return 1
  IFS='|' read -r kind expected <<<"$identity"
  case "$kind" in
    exe) process_executable_matches "$pid" "$expected" ;;
    arg)
      process_executable_is_python "$pid" &&
        process_has_exact_arg "$pid" "$expected"
      ;;
    *) return 1 ;;
  esac
}

service_process_pids() {
  local name="$1" proc pid
  if [[ -d /proc ]]; then
    for proc in /proc/[0-9]*; do
      pid="${proc##*/}"
      service_process_matches "$name" "$pid" && printf '%s\n' "$pid"
    done
    return 0
  fi
  while IFS= read -r pid; do
    pid="${pid//[[:space:]]/}"
    service_process_matches "$name" "$pid" && printf '%s\n' "$pid"
  done < <(ps -e -o pid= 2>/dev/null || true)
}

pid_owner_file() {
  printf '%s.owner\n' "$1"
}

pid_ready_file() {
  printf '%s.ready\n' "$1"
}

read_service_pidfile() {
  local pidfile="$1" pid=""
  [[ -f "$pidfile" && ! -L "$pidfile" ]] || return 1
  IFS= read -r pid <"$pidfile" || return 1
  safe_pid "$pid" || return 1
  printf '%s\n' "$pid"
}

read_service_owner_token() {
  local name="$1" pidfile="$2" owner token=""
  owner="$(pid_owner_file "$pidfile")"
  [[ -f "$owner" && ! -L "$owner" ]] || return 1
  IFS= read -r token <"$owner" || return 1
  [[ "$token" == "$name:"* ]] || return 1
  printf '%s\n' "$token"
}

process_has_owner_token() {
  local pid="$1" expected="$2" entry
  [[ -r "/proc/$pid/environ" ]] || return 1
  while IFS= read -r -d '' entry; do
    [[ "$entry" == "$MANAGED_PROCESS_TOKEN_ENV=$expected" ]] && return 0
  done <"/proc/$pid/environ"
  return 1
}

# Owner metadata is optional for processes created before token tracking was
# introduced. Once an owner file exists, however, it is a mandatory fence
# against treating a reused PID with the same executable as our process.
service_owner_matches() {
  local name="$1" pid="$2" pidfile="$3" owner token
  owner="$(pid_owner_file "$pidfile")"
  if [[ ! -e "$owner" && ! -L "$owner" ]]; then
    return 0
  fi
  token="$(read_service_owner_token "$name" "$pidfile")" || return 1
  process_has_owner_token "$pid" "$token"
}

service_process_can_be_stopped() {
  local name="$1" pid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local pidfile owner
  service_process_matches "$name" "$pid" || return 1
  if [[ "$fence_mode" == "token" ]]; then
    [[ "$expected_token" == "$name:"* ]] || return 1
    process_has_owner_token "$pid" "$expected_token"
    return $?
  fi
  [[ "$fence_mode" == "current" || "$fence_mode" == "legacy" ]] || return 1
  pidfile="$PID_DIR/$name.pid"
  owner="$(pid_owner_file "$pidfile")"
  if [[ -e "$owner" || -L "$owner" ]]; then
    service_owner_matches "$name" "$pid" "$pidfile"
    return $?
  fi
  return 0
}

# app-down removes stale pid/owner files before its port fallback runs. Capture
# whether the service was token-fenced at entry so that cleanup cannot silently
# downgrade it to legacy executable-only ownership.
capture_service_stop_fence() {
  local name="$1" mode_var="$2" token_var="$3" pidfile owner token=""
  local mode="legacy"
  pidfile="$PID_DIR/$name.pid"
  owner="$(pid_owner_file "$pidfile")"
  if [[ -e "$owner" || -L "$owner" ]]; then
    mode="token"
    token="$(read_service_owner_token "$name" "$pidfile" 2>/dev/null || true)"
    if [[ -z "$token" ]]; then
      echo "preserving invalid $name owner metadata as a fail-closed stop fence" >&2
    fi
  fi
  printf -v "$mode_var" '%s' "$mode"
  printf -v "$token_var" '%s' "$token"
}

restore_service_stop_fence() {
  local name="$1" fence_mode="$2" token="$3" pidfile owner owner_tmp status
  [[ "$fence_mode" == "token" ]] || return 0
  pidfile="$PID_DIR/$name.pid"
  owner="$(pid_owner_file "$pidfile")"
  owner_tmp="$owner.tmp.$BASHPID.$RANDOM"
  if printf '%s\n' "$token" >"$owner_tmp"; then
    :
  else
    status=$?
    rm -f "$owner_tmp" 2>/dev/null || true
    return "$status"
  fi
  if chmod 600 "$owner_tmp"; then
    :
  else
    status=$?
    rm -f "$owner_tmp" 2>/dev/null || true
    return "$status"
  fi
  if mv -f "$owner_tmp" "$owner"; then
    :
  else
    status=$?
    rm -f "$owner_tmp" 2>/dev/null || true
    return "$status"
  fi
}

process_stat_group() {
  local pid="$1" line fields state pgrp session
  [[ -r "/proc/$pid/stat" ]] || return 1
  line="$(<"/proc/$pid/stat")" || return 1
  fields="${line##*) }"
  read -r state _ pgrp session _ <<<"$fields" || return 1
  [[ -n "$pgrp" && -n "$session" ]] || return 1
  [[ "$state" != "Z" && "$state" != "X" ]] || return 1
  printf '%s|%s\n' "$pgrp" "$session"
}

process_parent_pid() {
  local pid="$1" line fields state parent
  if [[ -r "/proc/$pid/stat" ]]; then
    line="$(<"/proc/$pid/stat")" || return 1
    fields="${line##*) }"
    read -r state parent _ <<<"$fields" || return 1
  else
    parent="$(ps -p "$pid" -o ppid= 2>/dev/null)" || return 1
    parent="${parent//[[:space:]]/}"
  fi
  [[ "$parent" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$parent"
}

process_pid_running() {
  local pid="$1"
  safe_pid "$pid" || return 1
  if [[ -d /proc ]]; then
    process_stat_group "$pid" >/dev/null
    return $?
  fi
  kill -0 "$pid" 2>/dev/null
}

process_group_has_owner_token() {
  local pgid="$1" token="$2" proc pid group pgrp session
  [[ -d /proc ]] || return 1
  safe_pid "$pgid" || return 1
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    group="$(process_stat_group "$pid" 2>/dev/null || true)"
    [[ -n "$group" ]] || continue
    IFS='|' read -r pgrp session <<<"$group"
    if [[ "$pgrp" == "$pgid" && "$session" == "$pgid" ]] &&
      process_has_owner_token "$pid" "$token"; then
      return 0
    fi
  done
  return 1
}

# Ownership metadata is inherited through exec/fork, so a process group can be
# recovered safely even after its original leader has exited.
managed_process_group_matches() {
  local name="$1" pgid="$2" pidfile="$3" token
  token="$(read_service_owner_token "$name" "$pidfile")" || return 1
  process_group_has_owner_token "$pgid" "$token"
}

remove_service_state() {
  local pidfile="$1" owner ready
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  rm -f "$pidfile" || return $?
  rm -f "$owner" || return $?
  rm -f "$ready"
}

remove_stale_service_state() {
  local name="$1" pidfile="$2" pid="invalid"
  pid="$(read_service_pidfile "$pidfile" 2>/dev/null || true)"
  echo "removing stale pidfile for $name (pid=${pid:-invalid}; identity mismatch)" >&2
  remove_service_state "$pidfile"
}

# Prints a live, identity-checked PID. This function is intentionally read-only:
# status and migration guards must never race a concurrent start by deleting
# process ownership state.
validated_service_pid() {
  local name="$1" pidfile="$2" pid=""
  pid="$(read_service_pidfile "$pidfile")" || return 1
  if service_process_matches "$name" "$pid" &&
    service_owner_matches "$name" "$pid" "$pidfile"; then
    printf '%s\n' "$pid"
    return 0
  fi
  return 1
}

wait_topics() {
  local seconds="${1:-180}"
  local needed=(post-create post-update post-delete user-behavior-v2 message-push media-deleted)
  local i list ok t
  for ((i = 0; i < seconds; i += 2)); do
    list="$(docker exec xbh-rocketmq-broker sh -c 'sh mqadmin topicList -n rocketmq-namesrv:9876' 2>/dev/null || true)"
    ok=1
    for t in "${needed[@]}"; do
      if ! grep -qx "$t" <<<"$list"; then
        ok=0
        break
      fi
    done
    if [[ "$ok" -eq 1 ]]; then
      echo "ready: rocketmq topics"
      return 0
    fi
    sleep 2
  done
  echo "timeout waiting for rocketmq topics" >&2
  return 1
}

# docker-entrypoint-initdb.d only runs on an empty volume. Re-apply the
# idempotent analytics DDL so old ClickHouse volumes pick up new tables.
apply_analytics_schema() {
  local sql="$BACKEND/deploy/sql/xbh_analytics.sql"
  echo "applying ClickHouse analytics schema"
  docker exec -i xbh-clickhouse clickhouse-client --multiquery <"$sql"
}

mysql_root() {
  local pass="${MYSQL_ROOT_PASSWORD:-Xbh@MySQL2024!}"
  MYSQL_PWD="$pass" docker exec -i -e MYSQL_PWD xbh-mysql \
    mysql -uroot --default-character-set=utf8mb4 "$@"
}

mysql_value_hex() {
  local value="$1" encoded
  encoded="$(printf '%s' "$value" | od -An -v -tx1 | tr -d '[:space:]')"
  if [[ -z "$encoded" || ! "$encoded" =~ ^[0-9a-f]+$ ]]; then
    echo "failed to encode MySQL account value" >&2
    return 1
  fi
  printf '%s' "$encoded"
}

# Same empty-volume constraint as schema SQL. Seed the local test user on
# every middleware-up so existing MySQL volumes get admin/123456.
apply_dev_user() {
  local sql="$ROOT/deploy/dev/seed_dev_user.sql"
  echo "seeding local test user admin"
  mysql_root <"$sql"
}

# Application processes and black-box DB probes use separate accounts. The
# app account receives only runtime DML on the seven MySQL schemas; E2E gets
# read-only access. Passwords enter SQL as hex data and are quoted by MySQL,
# never interpolated as SQL literals or printed. Revoke-first keeps reused
# accounts from retaining grants issued by older stack versions.
apply_dev_db_grants() {
  validate_dev_db_env || return 1
  local app_user="$APP_MYSQL_USER" e2e_user="$E2E_MYSQL_USER"
  local app_pass_hex e2e_pass_hex
  app_pass_hex="$(mysql_value_hex "$APP_MYSQL_PASSWORD")" || return 1
  e2e_pass_hex="$(mysql_value_hex "$E2E_MYSQL_PASSWORD")" || return 1
  echo "seeding isolated app/e2e database accounts (${app_user}, ${e2e_user})"
  mysql_root <<SQL
CREATE DATABASE IF NOT EXISTS xbh_assistant DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS '${app_user}'@'%';
SET @app_password = CONVERT(X'${app_pass_hex}' USING utf8mb4);
SET @account_sql = CONCAT('ALTER USER ''${app_user}''@''%'' IDENTIFIED BY ', QUOTE(@app_password));
PREPARE account_stmt FROM @account_sql;
EXECUTE account_stmt;
DEALLOCATE PREPARE account_stmt;
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_content.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_user.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_interaction.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_media.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_message.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_feed.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_assistant.* TO '${app_user}'@'%';

CREATE USER IF NOT EXISTS '${e2e_user}'@'%';
SET @e2e_password = CONVERT(X'${e2e_pass_hex}' USING utf8mb4);
SET @account_sql = CONCAT('ALTER USER ''${e2e_user}''@''%'' IDENTIFIED BY ', QUOTE(@e2e_password));
PREPARE account_stmt FROM @account_sql;
EXECUTE account_stmt;
DEALLOCATE PREPARE account_stmt;
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${e2e_user}'@'%';
GRANT SELECT ON xbh_content.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_user.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_interaction.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_media.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_message.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_feed.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_assistant.* TO '${e2e_user}'@'%';

DROP USER IF EXISTS 'xbh'@'%';
DROP USER IF EXISTS 'xbh'@'localhost';
SQL
}

# Replay the backend's idempotent schema patches (deploy/sql/patches/) against
# the current MySQL volume. initdb.d only runs on an empty volume and no
# longer carries these files, so existing volumes would otherwise never pick
# them up. Every patch must be self-idempotent (see deploy/sql/README.md).
apply_sql_patches() {
  local dir="$BACKEND/deploy/sql/patches"
  [[ -d "$dir" ]] || return 0
  shopt -s nullglob
  local patches=("$dir"/*.sql)
  shopt -u nullglob
  if [[ ${#patches[@]} -eq 0 ]]; then
    return 0
  fi
  echo "applying ${#patches[@]} idempotent sql patch(es)"
  local sql
  for sql in "${patches[@]}"; do
    if [[ "${sql##*/}" == 20260829_assistant_runtime_v3.sql ]]; then
      require_safe_assistant_baseline || return $?
    fi
    mysql_root <"$sql" || return $?
  done
}

require_safe_assistant_baseline() {
  local marker_table marker_count tables table count
  marker_table="$(mysql_root -N -B -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='xbh_assistant' AND table_name='runtime_marker'")" || return $?
  if [[ "$marker_table" == 1 ]]; then
    marker_count="$(mysql_root -N -B -e "SELECT COUNT(*) FROM xbh_assistant.runtime_marker WHERE name='assistant_runtime_v3'")" || return $?
    if [[ "$marker_count" == 1 ]]; then
      return 0
    fi
  fi
  tables="$(mysql_root -N -B -e "SELECT table_name FROM information_schema.tables WHERE table_schema='xbh_assistant' AND table_type='BASE TABLE' ORDER BY table_name")" || return $?
  while IFS= read -r table; do
    [[ -n "$table" ]] || continue
    if [[ ! "$table" =~ ^[a-zA-Z0-9_]+$ ]]; then
      echo "refusing legacy Assistant reset with an unexpected table name" >&2
      return 1
    fi
    count="$(mysql_root -N -B -e "SELECT EXISTS(SELECT 1 FROM xbh_assistant.\`$table\` LIMIT 1)")" || return $?
    if [[ "$count" != 0 ]]; then
      echo "refusing legacy Assistant reset: migration marker missing and existing data found" >&2
      return 1
    fi
  done <<<"$tables"
}

require_apps_stopped_for_patches() {
  local operation="${1:-schema patches}"
  local recovery="${2:-run 'just app-down' before 'just middleware-up'}"
  local name pidfile pid key seen=" " running=()
  while IFS= read -r name; do
    [[ "$name" == "log-maintainer" ]] && continue
    pidfile="$PID_DIR/$name.pid"
    if pid="$(validated_service_pid "$name" "$pidfile")"; then
      key="$name:$pid"
      if [[ "$seen" != *" $key "* ]]; then
        running+=("$key")
        seen+="$key "
      fi
    elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
      managed_process_group_matches "$name" "$pid" "$pidfile"; then
      key="$name:group-$pid"
      if [[ "$seen" != *" $key "* ]]; then
        running+=("$key")
        seen+="$key "
      fi
    fi
  done < <(all_app_names)
  while IFS= read -r name; do
    [[ "$name" == "log-maintainer" ]] && continue
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      key="$name:$pid"
      if [[ "$seen" != *" $key "* ]]; then
        running+=("$key")
        seen+="$key "
      fi
    done < <(service_process_pids "$name")
  done < <(all_app_names)
  if port_open 127.0.0.1 "$GATEWAY_PORT"; then
    running+=("gateway-port:$GATEWAY_PORT")
  fi
  if [[ ${#running[@]} -gt 0 ]]; then
    echo "refusing $operation while app processes are running: ${running[*]}" >&2
    echo "$recovery" >&2
    return 1
  fi
}

# Load frozen eval/corpus.json (ids 1001-1300) and optional bulk
# backend eval/dev/corpus_2000.json (ids 2001-4000) into xbh_content.post.
# utf8mb4 is required; latin1 CLI charset double-encodes Chinese.
apply_eval_corpus() {
  local corpus="$BACKEND/eval/corpus.json"
  local bulk="$BACKEND/eval/dev/corpus_2000.json"
  local script="$BACKEND/scripts/seed_eval_corpus.py"
  local files=("$corpus")
  if [[ -f "$bulk" ]]; then
    files+=("$bulk")
  fi
  echo "seeding eval corpus from ${files[*]}"
  python3 "$script" "${files[@]}" | mysql_root || return $?
  mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN 1001 AND 1300;" </dev/null \
    | awk '{print "eval corpus posts 1001-1300: "$1}' || return $?
  mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN 2001 AND 4000;" </dev/null \
    | awk '{print "eval corpus posts 2001-4000: "$1}' || return $?
}

search_doc_count() {
  python3 - "$1" <<'PY' || echo 0
import json, sys, urllib.request
url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        print(int(json.load(resp).get("count") or 0))
except Exception:
    print(0)
PY
}

# Direct SQL inserts do not emit post-create MQ events. Rebuild ES when
# the index is behind published MySQL rows so search/assistant evals work.
maybe_rebuild_search() {
  load_env || return $?
  local corpus_n es_n
  corpus_n="$(mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN 1001 AND 4000 AND status = 1;" </dev/null | tr -d '[:space:]')" || return $?
  es_n="$(search_doc_count "http://127.0.0.1:9200/xbh_posts/_count" | tr -d '[:space:]')"
  if [[ -z "$corpus_n" || "$corpus_n" == "0" ]]; then
    echo "skip search rebuild: eval corpus not in mysql"
    return 0
  fi
  if [[ "${es_n:-0}" -ge "$corpus_n" ]]; then
    echo "search index already has ${es_n} docs (eval corpus=${corpus_n})"
    return 0
  fi
  echo "rebuilding search index (es=${es_n} eval corpus=${corpus_n})"
  (
    cd "$BACKEND" || exit $?
    go run ./app/search/mq/cmd/rebuild -f "$ETC_DIR/app/search/mq/etc/search-consumer.yaml"
  )
}

# True while a process group contains at least one runnable or sleeping member.
# Linux zombies no longer execute or own sockets, but kill -0 still reports
# them, so inspect /proc state there and use kill -0 as the portable fallback.
process_group_running() {
  local pgid="$1" proc pid group pgrp _
  if [[ -d /proc ]]; then
    for proc in /proc/[0-9]*; do
      pid="${proc##*/}"
      group="$(process_stat_group "$pid" 2>/dev/null || true)"
      [[ -n "$group" ]] || continue
      IFS='|' read -r pgrp _ <<<"$group"
      if [[ "$pgrp" == "$pgid" ]]; then
        return 0
      fi
    done
    return 1
  fi
  kill -0 -- "-$pgid" 2>/dev/null
}

stoppable_process_group_running() {
  local pgid="$1" token="${2:-}"
  process_group_running "$pgid" || return 1
  [[ -z "$token" ]] || process_group_has_owner_token "$pgid" "$token"
}

stop_owned_process_group() {
  local name="$1" pgid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local i pidfile owner token=""
  if [[ "$fence_mode" == "token" ]]; then
    if [[ "$expected_token" != "$name:"* ]]; then
      echo "refusing to stop $name group=$pgid: invalid owner token" >&2
      return 1
    fi
    token="$expected_token"
  elif [[ "$fence_mode" == "current" || "$fence_mode" == "legacy" ]]; then
    pidfile="$PID_DIR/$name.pid"
    owner="$(pid_owner_file "$pidfile")"
    if [[ -e "$owner" || -L "$owner" ]]; then
      token="$(read_service_owner_token "$name" "$pidfile")" || {
        echo "refusing to stop $name group=$pgid: invalid owner token" >&2
        return 1
      }
    fi
  else
    echo "refusing to stop $name group=$pgid: invalid stop fence mode" >&2
    return 1
  fi
  if ! process_group_running "$pgid"; then
    return 0
  fi
  if ! stoppable_process_group_running "$pgid" "$token"; then
    echo "refusing to stop $name group=$pgid: owner token mismatch" >&2
    return 1
  fi
  if ! kill -- "-$pgid" 2>/dev/null && stoppable_process_group_running "$pgid" "$token"; then
    echo "failed to send TERM to $name group=$pgid" >&2
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! stoppable_process_group_running "$pgid" "$token"; then
      if process_group_running "$pgid"; then
        echo "not escalating stop for $name group=$pgid: owner token changed" >&2
        return 1
      fi
      return 0
    fi
    sleep 0.2
  done
  if ! stoppable_process_group_running "$pgid" "$token"; then
    if process_group_running "$pgid"; then
      echo "not escalating stop for $name group=$pgid: owner token changed" >&2
      return 1
    fi
    return 0
  fi
  if ! kill -9 -- "-$pgid" 2>/dev/null && stoppable_process_group_running "$pgid" "$token"; then
    echo "failed to send KILL to $name group=$pgid" >&2
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! stoppable_process_group_running "$pgid" "$token"; then
      if process_group_running "$pgid"; then
        echo "$name group $pgid changed owner while stopping" >&2
        return 1
      fi
      return 0
    fi
    sleep 0.1
  done
  echo "$name process group $pgid survived KILL" >&2
  return 1
}

started_process_can_be_stopped() {
  local pid="$1" token="${2:-}" parent
  process_pid_running "$pid" || return 1
  [[ -z "$token" ]] && return 0
  process_has_owner_token "$pid" "$token" && return 0
  parent="$(process_parent_pid "$pid")" || return 1
  [[ "$parent" == "$BASHPID" ]]
}

# Fence cleanup with the launch token (or the still-direct child relation
# before exec publishes that token) so PID reuse cannot redirect signals.
stop_started_tree() {
  local name="$1" pid="$2" token="${3:-}" i group_owned=0
  if ! safe_pid "$pid"; then
    echo "cannot stop newly started $name: unsafe pid ${pid:-empty}" >&2
    return 1
  fi
  if process_group_running "$pid"; then
    if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
      echo "cannot stop newly started $name group=$pid: owner token mismatch" >&2
      return 1
    fi
    group_owned=1
  elif ! process_pid_running "$pid"; then
    return 0
  elif ! started_process_can_be_stopped "$pid" "$token"; then
    echo "cannot stop newly started $name pid=$pid: owner token mismatch" >&2
    return 1
  fi
  if [[ "$group_owned" == "1" ]]; then
    if ! kill -- "-$pid" 2>/dev/null && stoppable_process_group_running "$pid" "$token"; then
      echo "failed to send TERM to newly started $name group=$pid" >&2
      return 1
    fi
  else
    if ! kill "$pid" 2>/dev/null && started_process_can_be_stopped "$pid" "$token"; then
      echo "failed to send TERM to newly started $name pid=$pid" >&2
      return 1
    fi
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if process_group_running "$pid"; then
      if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
        echo "not escalating newly started $name group=$pid: owner token changed" >&2
        return 1
      fi
      if [[ "$group_owned" == "0" ]]; then
        group_owned=1
        if ! kill -- "-$pid" 2>/dev/null && stoppable_process_group_running "$pid" "$token"; then
          echo "failed to send TERM to newly started $name group=$pid" >&2
          return 1
        fi
      fi
    elif ! process_pid_running "$pid"; then
      return 0
    elif ! started_process_can_be_stopped "$pid" "$token"; then
      echo "not escalating newly started $name pid=$pid: owner token changed" >&2
      return 1
    fi
    sleep 0.1
  done
  if process_group_running "$pid"; then
    if [[ -n "$token" ]] && ! process_group_has_owner_token "$pid" "$token"; then
      echo "not escalating newly started $name group=$pid: owner token changed" >&2
      return 1
    fi
    group_owned=1
    if ! kill -9 -- "-$pid" 2>/dev/null && stoppable_process_group_running "$pid" "$token"; then
      echo "failed to send KILL to newly started $name group=$pid" >&2
      return 1
    fi
  elif process_pid_running "$pid"; then
    if ! started_process_can_be_stopped "$pid" "$token"; then
      echo "not escalating newly started $name pid=$pid: owner token changed" >&2
      return 1
    fi
    if ! kill -9 "$pid" 2>/dev/null && started_process_can_be_stopped "$pid" "$token"; then
      echo "failed to send KILL to newly started $name pid=$pid" >&2
      return 1
    fi
  else
    return 0
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if [[ "$group_owned" == "1" ]]; then
      if ! stoppable_process_group_running "$pid" "$token"; then
        if process_group_running "$pid"; then
          echo "newly started $name group $pid changed owner while stopping" >&2
          return 1
        fi
        return 0
      fi
    elif ! process_pid_running "$pid"; then
      return 0
    elif ! started_process_can_be_stopped "$pid" "$token"; then
      echo "newly started $name pid $pid changed owner while stopping" >&2
      return 1
    fi
    sleep 0.1
  done
  echo "newly started $name process tree $pid survived KILL" >&2
  return 1
}

new_managed_process_token() {
  local name="$1"
  printf '%s:%s:%s:%s\n' \
    "$name" "$BASHPID" "$RANDOM" "$(date +%s%N)"
}

track_app_started_service() {
  [[ "$APP_UP_TRACK_STARTS" == "1" ]] || return 0
  APP_UP_STARTED_SERVICES+=("$1")
}

prepare_service_start_state() {
  local name="$1" pidfile="$2" pid="" owner ready
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  if pid="$(read_service_pidfile "$pidfile" 2>/dev/null)"; then
    if managed_process_group_matches "$name" "$pid" "$pidfile"; then
      echo "stopping orphaned $name process group before restart (group=$pid)" >&2
      stop_svc "$name" || return $?
      return 0
    fi
    if service_process_matches "$name" "$pid" &&
      [[ -e "$owner" || -L "$owner" ]]; then
      echo "refusing to replace $name pid=$pid: owner token mismatch" >&2
      return 1
    fi
  fi
  if [[ -e "$pidfile" || -L "$pidfile" || -e "$owner" || -L "$owner" ||
    -e "$ready" || -L "$ready" ]]; then
    remove_stale_service_state "$name" "$pidfile" || return $?
  fi
}

record_started_pid() {
  local name="$1" pid="$2" pidfile="$3" token="$4"
  local owner pid_tmp owner_tmp status=0 cleanup_status=0
  owner="$(pid_owner_file "$pidfile")"
  pid_tmp="$pidfile.tmp.$BASHPID.$RANDOM"
  owner_tmp="$owner.tmp.$BASHPID.$RANDOM"

  printf '%s\n' "$token" >"$owner_tmp" || status=$?
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$owner_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    printf '%s\n' "$pid" >"$pid_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$pid_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    mv -f "$owner_tmp" "$owner" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    mv -f "$pid_tmp" "$pidfile" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    return 0
  fi

  echo "failed to record $name pid=$pid; stopping the newly started process" >&2
  stop_started_tree "$name" "$pid" "$token" || cleanup_status=$?
  rm -f "$pid_tmp" "$owner_tmp" 2>/dev/null || true
  if [[ "$cleanup_status" -eq 0 ]]; then
    remove_service_state "$pidfile" 2>/dev/null || true
  else
    # Preserve a usable recovery handle when possible if immediate cleanup
    # itself failed. Never replace the original pidfile error status.
    printf '%s\n' "$token" >"$owner" 2>/dev/null || true
    chmod 600 "$owner" 2>/dev/null || true
    printf '%s\n' "$pid" >"$pidfile" 2>/dev/null || true
    chmod 600 "$pidfile" 2>/dev/null || true
    echo "failed to stop unrecorded $name pid=$pid; manual cleanup required" >&2
  fi
  return "$status"
}

cleanup_failed_service_start() {
  local name="$1" pid="$2" pidfile="$3" token="${4:-}" stop_status
  if stop_started_tree "$name" "$pid" "$token"; then
    remove_service_state "$pidfile"
    return $?
  else
    stop_status=$?
  fi
  echo "keeping pidfile for $name because startup cleanup failed" >&2
  return "$stop_status"
}

assistant_agent_launch_matches() {
  local pid="$1" token="$2" pidfile="$PID_DIR/assistant-agent.pid"
  local current_pid current_token
  current_pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" || return 1
  [[ "$current_pid" == "$pid" ]] || return 1
  current_token="$(read_service_owner_token assistant-agent "$pidfile" 2>/dev/null)" || return 1
  [[ "$current_token" == "$token" ]] || return 1
  service_process_matches assistant-agent "$pid" || return 1
  process_has_owner_token "$pid" "$token"
}

process_listens_on_port() {
  if listening_port_owner_state "$1" "$2"; then
    return 0
  fi
  return 1
}

# Returns 0 when expected_pid is among the listeners, 1 while no listener PID
# is observable yet, and 2 when the port is explicitly owned by another PID.
listening_port_owner_state() {
  local expected_pid="$1" port="$2" pid found=0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    found=1
    [[ "$pid" == "$expected_pid" ]] && return 0
  done < <(listening_port_pids "$port")
  [[ "$found" -eq 0 ]] && return 1
  return 2
}

record_assistant_agent_ready() {
  local pid="$1" token="$2" pidfile="$PID_DIR/assistant-agent.pid"
  local ready ready_tmp status=0
  ready="$(pid_ready_file "$pidfile")"
  ready_tmp="$ready.tmp.$BASHPID.$RANDOM"
  printf '%s\n%s\n' "$pid" "$token" >"$ready_tmp" || status=$?
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$ready_tmp" || status=$?
  fi
  if [[ "$status" -eq 0 ]]; then
    mv -f "$ready_tmp" "$ready" || status=$?
  fi
  if [[ "$status" -ne 0 ]]; then
    rm -f "$ready_tmp" 2>/dev/null || true
  fi
  return "$status"
}

assistant_agent_ready_matches() {
  local pid="$1" token="$2" pidfile="$PID_DIR/assistant-agent.pid"
  local ready recorded_pid="" recorded_token=""
  ready="$(pid_ready_file "$pidfile")"
  [[ -f "$ready" && ! -L "$ready" ]] || return 1
  {
    IFS= read -r recorded_pid
    IFS= read -r recorded_token
  } <"$ready" || return 1
  [[ "$recorded_pid" == "$pid" && "$recorded_token" == "$token" ]] || return 1
  assistant_agent_launch_matches "$pid" "$token" || return 1
  process_listens_on_port "$pid" "$ASSISTANT_AGENT_METRICS_PORT" || return 1
  assistant_agent_launch_matches "$pid" "$token"
}

# go-zero launches the Prometheus server goroutine before the worker constructs
# its ServiceContext, but the listener may bind later. Bind readiness to this
# launch's immutable pid/token and the post-canary marker, then verify that the
# same process owns the metrics listener.
wait_assistant_agent_ready() {
  local pid="$1" token="$2" logfile="$3"
  local timeout="${4:-${ASSISTANT_AGENT_READY_TIMEOUT_SECONDS:-180}}"
  local label="${5:-assistant-agent}"
  local i iterations listener_status publish_status
  normalize_assistant_agent_metrics_port || return $?
  if [[ ! "$timeout" =~ ^[1-9][0-9]*$ ]]; then
    echo "invalid assistant-agent readiness timeout: $timeout" >&2
    return 1
  fi
  iterations=$((timeout * 10))
  for ((i = 0; i < iterations; i++)); do
    if ! assistant_agent_launch_matches "$pid" "$token"; then
      echo "$label exited or changed identity before readiness" >&2
      return 1
    fi
    if grep -Fqx -- "$ASSISTANT_AGENT_READY_LINE" "$logfile" 2>/dev/null; then
      if listening_port_owner_state "$pid" "$ASSISTANT_AGENT_METRICS_PORT"; then
        if ! assistant_agent_launch_matches "$pid" "$token"; then
          echo "$label changed identity while publishing readiness" >&2
          return 1
        fi
        if record_assistant_agent_ready "$pid" "$token"; then
          :
        else
          publish_status=$?
          echo "failed to record $label readiness" >&2
          return "$publish_status"
        fi
        if assistant_agent_ready_matches "$pid" "$token"; then
          echo "ready: $label"
          return 0
        fi
        echo "$label lost verified readiness while publishing readiness" >&2
        return 1
      else
        listener_status=$?
      fi
      if [[ "$listener_status" -eq 2 ]]; then
        echo "$label metrics listener on :$ASSISTANT_AGENT_METRICS_PORT is not owned by pid $pid" >&2
        return 1
      fi
    fi
    sleep 0.1 || return $?
  done
  echo "timeout waiting for $label post-canary readiness" >&2
  return 1
}

start_svc() {
  local name="$1" workdir="$2" bin="$3"
  shift 3
  local pidfile="$PID_DIR/$name.pid"
  local logfile="$LOG_DIR/$name.log"
  local bindir="$RUN_DIR/bin"
  local executable="$bindir/$name"
  local pid build_status token ready_status cleanup_status=0
  secure_runtime_paths || return $?
  touch "$logfile" || return $?
  chmod 600 "$logfile" || return $?
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
    if [[ "$name" == "assistant-agent" ]]; then
      normalize_assistant_agent_metrics_port || return $?
      token="$(read_service_owner_token "$name" "$pidfile")" || return $?
      if ! assistant_agent_ready_matches "$pid" "$token"; then
        echo "assistant-agent pid=$pid is running without verified post-canary readiness; restart it" >&2
        return 1
      fi
    fi
    echo "already running: $name pid=$pid"
    return 0
  fi
  prepare_service_start_state "$name" "$pidfile" || return $?
  echo "building $name"
  if (
    cd "$workdir" || exit $?
    go build -o "$executable.tmp" "$bin"
  ) >>"$logfile" 2>&1; then
    build_status=0
  else
    build_status=$?
  fi
  [[ "$build_status" -eq 0 ]] || return "$build_status"
  mv -f "$executable.tmp" "$executable" || return $?
  if [[ "$name" == "assistant-agent" ]]; then
    : >"$logfile" || return $?
    chmod 600 "$logfile" || return $?
    rm -f "$logfile.1.gz" || return $?
  fi
  echo "starting $name"
  token="$(new_managed_process_token "$name")" || return $?
  (
    local started_pid
    cd "$workdir" || exit $?
    (
      close_app_lifecycle_lock_fd || exit $?
      exec env "$MANAGED_PROCESS_TOKEN_ENV=$token" setsid "$executable" "$@"
    ) >>"$logfile" 2>&1 </dev/null &
    started_pid=$!
    record_started_pid "$name" "$started_pid" "$pidfile" "$token" || exit $?
  ) || return $?
  sleep 0.1
  pid="$(<"$pidfile")"
  if ! validated_service_pid "$name" "$pidfile" >/dev/null; then
    if cleanup_failed_service_start "$name" "$pid" "$pidfile" "$token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "$name exited during startup; see $logfile" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  if [[ "$name" == "assistant-agent" ]]; then
    if wait_assistant_agent_ready "$pid" "$token" "$logfile"; then
      :
    else
      ready_status=$?
      if cleanup_failed_service_start "$name" "$pid" "$pidfile" "$token"; then
        :
      else
        cleanup_status=$?
      fi
      echo "$name failed post-canary readiness; see $logfile" >&2
      [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
      return "$ready_status"
    fi
  fi
  track_app_started_service "$name"
}

start_log_maintainer() {
  local pidfile="$PID_DIR/log-maintainer.pid"
  local pid started_pid token cleanup_status=0
  secure_runtime_paths || return $?
  if pid="$(validated_service_pid log-maintainer "$pidfile")"; then
    return 0
  fi
  prepare_service_start_state log-maintainer "$pidfile" || return $?
  token="$(new_managed_process_token log-maintainer)" || return $?
  (
    close_app_lifecycle_lock_fd || exit $?
    exec env "$MANAGED_PROCESS_TOKEN_ENV=$token" \
      setsid python3 "$ROOT/deploy/dev/log_maintainer.py" "$LOG_DIR" \
      --max-bytes "$LOG_MAX_BYTES" --interval "$LOG_ROTATE_INTERVAL_SECONDS"
  ) >/dev/null 2>&1 </dev/null &
  started_pid=$!
  record_started_pid log-maintainer "$started_pid" "$pidfile" "$token" || return $?
  sleep 0.1
  pid="$(<"$pidfile")"
  if ! validated_service_pid log-maintainer "$pidfile" >/dev/null; then
    if cleanup_failed_service_start log-maintainer "$pid" "$pidfile" "$token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "log maintainer exited during startup" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  track_app_started_service log-maintainer
}

start_row() {
  local row="$1"
  IFS='|' read -r name workdir bin flag conf <<<"$row"
  start_svc "$name" "$workdir" "$bin" "$flag" "$conf"
}

stop_tree() {
  local name="$1" pid="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local i group_owned=0
  if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    if service_process_matches "$name" "$pid"; then
      echo "refusing to stop $name pid=$pid: owner token mismatch" >&2
      return 1
    fi
    return 0
  fi
  if process_group_running "$pid"; then
    group_owned=1
  fi
  if [[ "$group_owned" == "1" ]]; then
    stop_owned_process_group "$name" "$pid" "$fence_mode" "$expected_token"
    return $?
  else
    if ! kill "$pid" 2>/dev/null &&
      service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      echo "failed to send TERM to $name pid=$pid" >&2
      return 1
    fi
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      if service_process_matches "$name" "$pid"; then
        echo "not escalating stop for $name: pid $pid owner token changed" >&2
        return 1
      fi
      echo "not escalating stop for $name: pid $pid no longer matches" >&2
      return 0
    fi
    sleep 0.2
  done
  if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    if service_process_matches "$name" "$pid"; then
      echo "not escalating stop for $name: pid $pid owner token changed" >&2
      return 1
    fi
    return 0
  fi
  if ! kill -9 "$pid" 2>/dev/null &&
    service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
    echo "failed to send KILL to $name pid=$pid" >&2
    return 1
  fi
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      if service_process_matches "$name" "$pid"; then
        echo "$name pid $pid changed owner while stopping" >&2
        return 1
      fi
      return 0
    fi
    sleep 0.1
  done
  echo "$name process tree $pid survived KILL" >&2
  return 1
}

stop_svc() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  local pid="" owner ready stop_status
  owner="$(pid_owner_file "$pidfile")"
  ready="$(pid_ready_file "$pidfile")"
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
    echo "stopping $name pid=$pid"
    if stop_tree "$name" "$pid"; then
      :
    else
      stop_status=$?
      echo "keeping pidfile for $name because the process did not stop" >&2
      return "$stop_status"
    fi
  elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
    managed_process_group_matches "$name" "$pid" "$pidfile"; then
    echo "stopping orphaned $name group=$pid"
    if stop_owned_process_group "$name" "$pid"; then
      :
    else
      stop_status=$?
      echo "keeping pidfile for $name because the process group did not stop" >&2
      return "$stop_status"
    fi
  elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
    service_process_matches "$name" "$pid" &&
    [[ -e "$owner" || -L "$owner" ]]; then
    echo "refusing to stop $name pid=$pid: owner token mismatch; keeping pidfile" >&2
    return 1
  elif [[ -e "$pidfile" || -L "$pidfile" || -e "$owner" || -L "$owner" ||
    -e "$ready" || -L "$ready" ]]; then
    remove_stale_service_state "$name" "$pidfile" || return $?
    return 0
  else
    return 0
  fi
  remove_service_state "$pidfile"
}

assistant_agent_row() {
  local row
  for row in "${MQ_SERVICES[@]}"; do
    if [[ "$row" == assistant-agent\|* ]]; then
      printf '%s\n' "$row"
      return 0
    fi
  done
  echo "assistant-agent service row is missing" >&2
  return 1
}

restore_agent_after_fixture() {
  [[ "$AGENT_FIXTURE_RESTORE" == "1" ]] || return 0
  AGENT_FIXTURE_RESTORE=0
  local restore_status=0 step_status=0 can_start=1 row
  if stop_svc assistant-agent; then
    :
  else
    restore_status=$?
    can_start=0
  fi
  if stop_svc llm-fixture; then
    :
  else
    step_status=$?
    [[ "$restore_status" -ne 0 ]] || restore_status="$step_status"
  fi
  if ensure_assistant_db_env; then
    :
  else
    step_status=$?
    [[ "$restore_status" -ne 0 ]] || restore_status="$step_status"
    can_start=0
  fi
  if [[ "$can_start" == "1" ]] && row="$(assistant_agent_row)"; then
    if ! start_row "$row"; then
      echo "failed to restore assistant-agent; run just app-up" >&2
      [[ "$restore_status" -ne 0 ]] || restore_status=1
    fi
  elif [[ "$can_start" == "1" ]]; then
    [[ "$restore_status" -ne 0 ]] || restore_status=1
  else
    echo "skipping assistant-agent restart because fixture cleanup failed" >&2
  fi
  return "$restore_status"
}

run_agent_reset_test_with_fixture() {
  local agent_row="$1" scenario="${2:-reset}" test_path reset_flag=0 research_flag=0
  case "$scenario" in
    reset) test_path="$ROOT/deploy/dev/e2e/test_assistant.py::test_agent_stream_reset_replay"; reset_flag=1 ;;
    research) test_path="$ROOT/deploy/dev/e2e/test_assistant_research.py"; research_flag=1 ;;
    *) echo "unsupported assistant fixture scenario" >&2; return 2 ;;
  esac
  stop_svc assistant-agent || return $?
  (
    export ASSISTANT_LLM_ENABLED=true
    export ASSISTANT_LLM_WIRE_API=responses
    export ASSISTANT_LLM_ENDPOINT="http://127.0.0.1:$AGENT_FIXTURE_PORT/v1"
    export ASSISTANT_LLM_API_KEY=""
    export ASSISTANT_LLM_MODEL=fixture-model
    export ASSISTANT_LLM_MODEL_SMALL=""
    export ASSISTANT_LLM_REVIEW_MODEL=""
    export ASSISTANT_LLM_PROMPT_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_COMPLETION_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_CACHE_READ_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_CACHE_WRITE_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_REASONING_COST_PER_MILLION_TOKENS=0
    export ASSISTANT_LLM_FALLBACK_ENABLED=false
    if [[ "$scenario" == research ]]; then
      export TAVILY_API_KEY=fixture-only
      export TAVILY_ENDPOINT="http://127.0.0.1:$AGENT_FIXTURE_PORT"
    fi
    start_row "$agent_row"
  ) || return $?
  E2E_EXPECT_ASSISTANT_RESET="$reset_flag" E2E_EXPECT_ASSISTANT_RESEARCH="$research_flag" PYTHONDONTWRITEBYTECODE=1 \
    python3 -m pytest -v \
    "$test_path"
}

e2e_agent_reset_locked() {
  local scenario="${1:-reset}"
  load_env || return $?
  ensure_assistant_db_env || return $?
  secure_runtime_paths || return $?
  local agent_row fixture_pidfile fixture_log fixture_pid fixture_token agent_pidfile agent_pid
  local test_status=0 restore_status=0 cleanup_status=0
  agent_row="$(assistant_agent_row)" || return $?
  agent_pidfile="$PID_DIR/assistant-agent.pid"
  if ! agent_pid="$(validated_service_pid assistant-agent "$agent_pidfile")"; then
    echo "assistant-agent must be running before the reset fixture gate" >&2
    return 1
  fi

  stop_svc llm-fixture || return $?
  fixture_pidfile="$PID_DIR/llm-fixture.pid"
  fixture_log="$LOG_DIR/llm-fixture.log"
  : >"$fixture_log" || return $?
  chmod 600 "$fixture_log" || return $?
  fixture_token="$(new_managed_process_token llm-fixture)" || return $?
  (
    close_app_lifecycle_lock_fd || exit $?
    exec env "$MANAGED_PROCESS_TOKEN_ENV=$fixture_token" \
      setsid python3 "$ROOT/deploy/dev/e2e/fixtures/llm_provider.py" \
      --port "$AGENT_FIXTURE_PORT" --strict
  ) >>"$fixture_log" 2>&1 </dev/null &
  fixture_pid=$!
  record_started_pid llm-fixture "$fixture_pid" "$fixture_pidfile" "$fixture_token" || return $?
  sleep 0.1
  if ! validated_service_pid llm-fixture "$fixture_pidfile" >/dev/null; then
    if cleanup_failed_service_start llm-fixture "$fixture_pid" "$fixture_pidfile" "$fixture_token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "llm fixture exited during startup; see $fixture_log" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  if wait_http "http://127.0.0.1:$AGENT_FIXTURE_PORT/health" 30 llm-fixture; then
    :
  else
    test_status=$?
    stop_svc llm-fixture || return $?
    return "$test_status"
  fi

  AGENT_FIXTURE_RESTORE=1
  trap restore_agent_after_fixture EXIT
  if run_agent_reset_test_with_fixture "$agent_row" "$scenario"; then
    test_status=0
  else
    test_status=$?
  fi
  restore_agent_after_fixture || restore_status=$?
  trap - EXIT
  if [[ "$test_status" -ne 0 ]]; then
    return "$test_status"
  fi
  return "$restore_status"
}

e2e_agent_reset() {
  with_app_lifecycle_lock exclusive e2e_agent_reset_locked
}

e2e_agent_research() {
  with_app_lifecycle_lock exclusive e2e_agent_reset_locked research
}

all_app_names() {
  local row name
  for row in "${RPC_SERVICES[@]}" "${MQ_SERVICES[@]}"; do
    IFS='|' read -r name _ <<<"$row"
    printf '%s\n' "$name"
  done
  printf '%s\n' gateway frontend log-maintainer
}

validate_all_app_processes() {
  local name pidfile
  while IFS= read -r name; do
    pidfile="$PID_DIR/$name.pid"
    if ! validated_service_pid "$name" "$pidfile" >/dev/null; then
      echo "$name is not running after application startup" >&2
      return 1
    fi
  done < <(all_app_names)
}

middleware_up_locked() {
  load_env || return $?
  secure_runtime_paths || return $?
  require_apps_stopped_for_patches || return $?
  require_compose_version || return $?
  echo "starting middleware containers"
  compose up -d || return $?
  wait_port 127.0.0.1 3306 90 mysql || return $?
  apply_dev_user || return $?
  apply_sql_patches || return $?
  apply_dev_db_grants || return $?
  apply_eval_corpus || return $?
  wait_port 127.0.0.1 6379 60 redis || return $?
  wait_port 127.0.0.1 2379 60 etcd || return $?
  wait_port 127.0.0.1 9200 90 elasticsearch || return $?
  wait_port 127.0.0.1 9876 90 rocketmq-namesrv || return $?
  wait_port 127.0.0.1 10911 180 rocketmq-broker || return $?
  wait_topics 180 || return $?
  wait_port 127.0.0.1 8123 60 clickhouse || return $?
  apply_analytics_schema || return $?
  wait_http "http://127.0.0.1:3100/ready" 90 loki || return $?
  wait_port 127.0.0.1 9333 60 seaweedfs-master || true
}

middleware_up() {
  with_app_lifecycle_lock exclusive middleware_up_locked
}

middleware_down_locked() {
  # Containers only; the :3002 proxy is app-layer and belongs to proxy_down.
  require_apps_stopped_for_patches \
    "middleware shutdown" "run 'just app-down' before 'just middleware-down'" || return $?
  echo "stopping middleware containers (volumes kept)"
  compose stop || return $?
}

middleware_down() {
  with_app_lifecycle_lock exclusive middleware_down_locked
}

# Opt-in algorithm services live behind the compose profile "algorithm" so
# middleware-only stacks skip model downloads and inference ports. recommend-rpc
# already dials 127.0.0.1:9025 (ONLINE_INFER_ENDPOINT); until these containers
# are up it degrades to rule-based ranking.
algorithm_up_locked() {
  load_env || return $?
  require_compose_version || return $?
  echo "starting algorithm containers (embedding-service, online-infer)"
  COMPOSE_PROFILES=algorithm compose up -d || return $?
  wait_port 127.0.0.1 9025 300 online-infer || return $?
}

algorithm_up() {
  with_app_lifecycle_lock exclusive algorithm_up_locked
}

algorithm_down_locked() {
  echo "stopping algorithm containers"
  COMPOSE_PROFILES=algorithm compose stop online-infer embedding-service || return $?
}

algorithm_down() {
  with_app_lifecycle_lock exclusive algorithm_down_locked
}

# Local Flutter web engine assets (CanvasKit/Skwasm). Since Flutter 3.44 the
# engine reads its base URL from a compile-time dart-define only, so the dev
# server must serve the assets itself: symlink the SDK cache into the app's
# web/ dir and pass --dart-define=FLUTTER_WEB_CANVASKIT_URL=/canvaskit/.
# The link re-points on every app-up, following SDK upgrades automatically.
ensure_web_canvaskit() {
  local fl sdk src dst
  fl="$(command -v flutter 2>/dev/null || true)"
  [[ -n "$fl" ]] || return 1
  sdk="$(cd "$(dirname "$(readlink -f "$fl")")/.." && pwd)"
  src="$sdk/bin/cache/flutter_web_sdk/canvaskit"
  [[ -d "$src" ]] || return 1
  dst="$FRONTEND/web/canvaskit"
  mkdir -p "$FRONTEND/web" || return $?
  if [[ "$(readlink -f "$dst" 2>/dev/null)" != "$src" ]]; then
    rm -f "$dst" || return $?
    ln -s "$src" "$dst" || return $?
  fi
  [[ -e "$dst/canvaskit.js" ]]
}

proxy_up() {
  local was_running=0 running_state container_names status
  if container_names="$(docker ps -a --format '{{.Names}}')"; then
    :
  else
    status=$?
    echo "failed to list Docker containers while starting $PROXY_NAME" >&2
    return "$status"
  fi
  if grep -Fxq -- "$PROXY_NAME" <<<"$container_names"; then
    running_state="$(docker inspect -f '{{.State.Running}}' "$PROXY_NAME")" || return $?
    [[ "$running_state" == "true" ]] && was_running=1
    docker rm -f "$PROXY_NAME" >/dev/null || return $?
  fi
  echo "starting $PROXY_NAME on :$ENTRY_PORT"
  docker run -d --name "$PROXY_NAME" --network host --restart unless-stopped \
    -v "$PROXY_CONF:/etc/nginx/nginx.conf:ro" \
    nginx:stable-alpine >/dev/null || return $?
  if [[ "$was_running" == "0" ]]; then
    track_app_started_service proxy
  fi
  running_state="$(docker inspect -f '{{.State.Running}}' "$PROXY_NAME")" || return $?
  if [[ "$running_state" != "true" ]]; then
    echo "$PROXY_NAME exited during startup; check its Docker logs" >&2
    return 1
  fi
}

proxy_down() {
  local container_names status
  if container_names="$(docker ps -a --format '{{.Names}}')"; then
    :
  else
    status=$?
    echo "failed to list Docker containers while stopping $PROXY_NAME" >&2
    return "$status"
  fi
  if grep -Fxq -- "$PROXY_NAME" <<<"$container_names"; then
    echo "stopping $PROXY_NAME"
    docker rm -f "$PROXY_NAME" >/dev/null || return $?
  fi
}

frontend_up() {
  local pidfile="$PID_DIR/frontend.pid"
  local logfile="$LOG_DIR/frontend.log"
  local pid build_status token cleanup_status=0
  secure_runtime_paths || return $?
  touch "$logfile" || return $?
  chmod 600 "$logfile" || return $?
  if pid="$(validated_service_pid frontend "$pidfile")"; then
    echo "already running: frontend pid=$pid"
    return 0
  fi
  prepare_service_start_state frontend "$pidfile" || return $?

  # CanvasKit base reaches the release build as a compile-time dart-define;
  # prefer same-origin assets (web/canvaskit -> SDK cache), fall back to the
  # engine-revision-pinned gstatic URL when the SDK cache is unavailable.
  local canvaskit_url=""
  if ensure_web_canvaskit; then
    canvaskit_url="/canvaskit/"
  else
    echo "warning: flutter_web_sdk canvaskit dir not found, using gstatic fallback" >&2
    local fl sdk rev stamp
    fl="$(command -v flutter 2>/dev/null || true)"
    sdk=""
    if [[ -n "$fl" ]]; then
      sdk="$(cd "$(dirname "$(readlink -f "$fl")")/.." 2>/dev/null && pwd || true)"
    fi
    stamp="$sdk/bin/cache/engine_stamp.json"
    if [[ -f "$stamp" ]]; then
      rev="$(sed -n 's/.*"git_revision": *"\([^"]*\)".*/\1/p' "$stamp" | head -n1)"
      [[ -n "$rev" ]] && canvaskit_url="https://www.gstatic.com/flutter-canvaskit/${rev}/"
    fi
  fi

  local bundle="$FRONTEND/build/web"
  local needs_build=0
  if [[ "${FORCE_FRONT_BUILD:-0}" == "1" || ! -f "$bundle/index.html" ]]; then
    needs_build=1
  elif ! front_bundle_fresh; then
    needs_build=1
  fi

  if [[ "$needs_build" == "1" ]]; then
    echo "building frontend (release)..."
    if (
      cd "$FRONTEND" || exit $?
      env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
        -u ALL_PROXY -u all_proxy \
        flutter build web --release -t lib/main.dart \
        --no-web-resources-cdn \
        --dart-define=FLUTTER_WEB_CANVASKIT_URL="${canvaskit_url:-/canvaskit/}"
    ) >>"$logfile" 2>&1; then
      build_status=0
    else
      build_status=$?
    fi
    if [[ "$build_status" -ne 0 ]]; then
      rm -f "$RUN_DIR/front-build.stamp"
      echo "frontend build failed; refusing to serve an existing bundle; see $logfile" >&2
      return "$build_status"
    fi
    touch "$RUN_DIR/front-build.stamp" || return $?
  else
    echo "frontend bundle up to date; set FORCE_FRONT_BUILD=1 to rebuild"
  fi

  echo "starting frontend on :$FRONT_PORT (static release bundle)"
  token="$(new_managed_process_token frontend)" || return $?
  (
    local started_pid
    cd "$FRONTEND" || exit $?
    (
      close_app_lifecycle_lock_fd || exit $?
      exec env "$MANAGED_PROCESS_TOKEN_ENV=$token" \
        setsid python3 "$ROOT/deploy/dev/serve_release.py" "$FRONT_PORT" "$bundle"
    ) >>"$logfile" 2>&1 </dev/null &
    started_pid=$!
    record_started_pid frontend "$started_pid" "$pidfile" "$token" || exit $?
  ) || return $?
  sleep 0.1
  pid="$(<"$pidfile")"
  if ! validated_service_pid frontend "$pidfile" >/dev/null; then
    if cleanup_failed_service_start frontend "$pid" "$pidfile" "$token"; then
      :
    else
      cleanup_status=$?
    fi
    echo "frontend exited during startup; see $logfile" >&2
    [[ "$cleanup_status" -eq 0 ]] || return "$cleanup_status"
    return 1
  fi
  track_app_started_service frontend
}

# True when no tracked frontend source is newer than the last build stamp.
front_bundle_fresh() {
  local stamp="$RUN_DIR/front-build.stamp"
  [[ -f "$stamp" ]] || return 1
  local changed roots=("$FRONTEND/lib" "$FRONTEND/web" "$FRONTEND/pubspec.yaml" "$FRONTEND/pubspec.lock")
  [[ -d "$FRONTEND/assets" ]] && roots+=("$FRONTEND/assets")
  changed="$(find "${roots[@]}" \
    -type f -newer "$stamp" -print -quit 2>/dev/null)" || return $?
  [[ -z "$changed" ]]
}

app_up_steps() {
  load_env || return $?
  ensure_assistant_db_env || return $?
  secure_runtime_paths || return $?
  clear_sensitive_assistant_logs || return $?
  wipe_legacy_assistant_redis || return $?
  prepare_etc || return $?
  start_log_maintainer || return $?
  local row
  for row in "${RPC_SERVICES[@]}"; do
    start_row "$row" || return $?
  done
  wait_port 127.0.0.1 9090 240 user-rpc || return $?
  wait_topics 180 || return $?
  for row in "${MQ_SERVICES[@]}"; do
    start_row "$row" || return $?
  done
  start_svc gateway "$BACKEND" ./app/gateway -f "$ETC_DIR/app/gateway/etc/gateway.yaml" || return $?
  frontend_up || return $?
  proxy_up || return $?
  wait_port 127.0.0.1 "$GATEWAY_PORT" 240 gateway || return $?
  wait_port 127.0.0.1 "$FRONT_PORT" 240 frontend || return $?
  wait_http "http://127.0.0.1:$ENTRY_PORT/" 60 proxy-entry || return $?
  maybe_rebuild_search || return $?
  validate_all_app_processes || return $?
  echo "entry http://127.0.0.1:$ENTRY_PORT/  (page=$(http_code "http://127.0.0.1:$ENTRY_PORT/") api=$(http_code "http://127.0.0.1:$ENTRY_PORT/api/v1/"))"
}

rollback_app_starts() {
  local index name step_status status=0
  for ((index = ${#APP_UP_STARTED_SERVICES[@]} - 1; index >= 0; index--)); do
    name="${APP_UP_STARTED_SERVICES[index]}"
    if [[ "$name" == "proxy" ]]; then
      if proxy_down; then
        :
      else
        step_status=$?
        [[ "$status" -ne 0 ]] || status="$step_status"
      fi
    elif stop_svc "$name"; then
      :
    else
      step_status=$?
      [[ "$status" -ne 0 ]] || status="$step_status"
    fi
  done
  return "$status"
}

app_up_locked() {
  local status rollback_status=0
  APP_UP_STARTED_SERVICES=()
  APP_UP_TRACK_STARTS=1
  if app_up_steps; then
    APP_UP_TRACK_STARTS=0
    return 0
  else
    status=$?
  fi
  APP_UP_TRACK_STARTS=0

  echo "application startup failed with status $status; rolling back" >&2
  rollback_app_starts || rollback_status=$?
  if [[ "$rollback_status" -ne 0 ]]; then
    echo "application rollback also failed with status $rollback_status" >&2
  fi
  return "$status"
}

app_up() {
  with_app_lifecycle_lock exclusive app_up_locked
}

listening_port_pids() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    (ss -H -ltnp "sport = :$port" 2>/dev/null || true) | awk -v port="$port" '
      {
        local_address = $4
        if (local_address != "127.0.0.1:" port &&
            local_address != "0.0.0.0:" port &&
            local_address != "*:" port &&
            local_address != "[::]:" port &&
            local_address != "[::ffff:127.0.0.1]:" port) {
          next
        }
        line = $0
        while (match(line, /pid=[0-9]+/)) {
          print substr(line, RSTART + 4, RLENGTH - 4)
          line = substr(line, RSTART + RLENGTH)
        }
      }' | sort -u
    return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -t -iTCP@127.0.0.1:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true
  fi
}

stop_owned_port() {
  local name="$1" port="$2" fence_mode="${3:-current}" expected_token="${4:-}"
  local pid found=0 step_status status=0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    found=1
    if service_process_can_be_stopped "$name" "$pid" "$fence_mode" "$expected_token"; then
      echo "stopping orphaned $name on :$port pid=$pid"
      if stop_tree "$name" "$pid" "$fence_mode" "$expected_token"; then
        :
      else
        step_status=$?
        [[ "$status" -ne 0 ]] || status="$step_status"
      fi
    else
      echo "leaving unknown process on :$port pid=$pid (not managed $name)" >&2
      [[ "$status" -ne 0 ]] || status=1
    fi
  done < <(listening_port_pids "$port")
  if port_open 127.0.0.1 "$port"; then
    if [[ "$found" == "0" ]]; then
      echo "leaving unknown process on :$port (owner could not be verified)" >&2
    else
      echo "critical port :$port remains occupied after stopping $name" >&2
    fi
    [[ "$status" -ne 0 ]] || status=1
  fi
  return "$status"
}

app_down_locked() {
  local name step_status status=0
  local frontend_fence_mode frontend_fence_token gateway_fence_mode gateway_fence_token
  capture_service_stop_fence frontend frontend_fence_mode frontend_fence_token || return $?
  capture_service_stop_fence gateway gateway_fence_mode gateway_fence_token || return $?
  while IFS= read -r name; do
    if stop_svc "$name"; then
      :
    else
      step_status=$?
      [[ "$status" -ne 0 ]] || status="$step_status"
    fi
  done < <(all_app_names | tac)
  if proxy_down; then
    :
  else
    step_status=$?
    [[ "$status" -ne 0 ]] || status="$step_status"
  fi
  if stop_owned_port frontend "$FRONT_PORT" "$frontend_fence_mode" "$frontend_fence_token"; then
    :
  else
    step_status=$?
    if ! restore_service_stop_fence frontend "$frontend_fence_mode" "$frontend_fence_token"; then
      echo "failed to restore frontend owner fence after port cleanup failure" >&2
    fi
    [[ "$status" -ne 0 ]] || status="$step_status"
  fi
  if stop_owned_port gateway "$GATEWAY_PORT" "$gateway_fence_mode" "$gateway_fence_token"; then
    :
  else
    step_status=$?
    if ! restore_service_stop_fence gateway "$gateway_fence_mode" "$gateway_fence_token"; then
      echo "failed to restore gateway owner fence after port cleanup failure" >&2
    fi
    [[ "$status" -ne 0 ]] || status="$step_status"
  fi
  return "$status"
}

app_down() {
  with_app_lifecycle_lock exclusive app_down_locked
}

pid_state() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  if [[ ! -e "$pidfile" && ! -L "$pidfile" ]]; then
    printf '%-18s %s\n' "$name" "no-pid"
    return
  fi
  local pid token
  if pid="$(validated_service_pid "$name" "$pidfile")"; then
    if [[ "$name" == "assistant-agent" ]]; then
      token="$(read_service_owner_token "$name" "$pidfile" 2>/dev/null || true)"
      if [[ -n "$token" ]] && normalize_assistant_agent_metrics_port &&
        assistant_agent_ready_matches "$pid" "$token"; then
        printf '%-18s alive ready pid=%s\n' "$name" "$pid"
      else
        printf '%-18s alive UNREADY pid=%s\n' "$name" "$pid"
      fi
    else
      printf '%-18s alive pid=%s\n' "$name" "$pid"
    fi
  elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
    managed_process_group_matches "$name" "$pid" "$pidfile"; then
    printf '%-18s ORPHAN group=%s\n' "$name" "$pid"
  else
    printf '%-18s %s\n' "$name" "stale-pid"
  fi
}

stack_status_locked() {
  normalize_assistant_agent_metrics_port || return $?
  echo "== containers =="
  compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}' || true
  if docker inspect "$PROXY_NAME" >/dev/null 2>&1; then
    echo "$PROXY_NAME $(docker inspect -f '{{.State.Status}}' "$PROXY_NAME")"
  else
    echo "$PROXY_NAME absent"
  fi
  echo
  echo "== app pids =="
  local name
  while IFS= read -r name; do
    pid_state "$name"
  done < <(all_app_names)
  echo
  echo "== ports =="
  printf ':%s nginx  %s\n' "$ENTRY_PORT" "$(http_code "http://127.0.0.1:$ENTRY_PORT/")"
  printf ':%s api    %s\n' "$ENTRY_PORT" "$(http_code "http://127.0.0.1:$ENTRY_PORT/api/v1/")"
  printf ':%s gw     %s\n' "$GATEWAY_PORT" "$(http_code "http://127.0.0.1:$GATEWAY_PORT/")"
  printf ':%s front  %s\n' "$FRONT_PORT" "$(http_code "http://127.0.0.1:$FRONT_PORT/")"
  printf ':3100 loki  %s\n' "$(http_code "http://127.0.0.1:3100/ready")"
  printf ':%s agent %s\n' "$ASSISTANT_AGENT_METRICS_PORT" \
    "$(http_code "http://127.0.0.1:$ASSISTANT_AGENT_METRICS_PORT/metrics")"
}

stack_status() {
  with_app_lifecycle_lock shared stack_status_locked
}

stack_up_locked() {
  app_down_locked || return $?
  middleware_up_locked || return $?
  app_up_locked || return $?
  stack_status_locked || return $?
}

stack_up() {
  with_app_lifecycle_lock exclusive stack_up_locked
}

stack_down_locked() {
  app_down_locked || return $?
  middleware_down_locked || return $?
  echo "stopped"
}

stack_down() {
  with_app_lifecycle_lock exclusive stack_down_locked
}

stack_restart_locked() {
  stack_down_locked || return $?
  stack_up_locked || return $?
}

stack_restart() {
  with_app_lifecycle_lock exclusive stack_restart_locked
}
