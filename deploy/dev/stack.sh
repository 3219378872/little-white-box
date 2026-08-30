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

load_env() {
  local file=""
  if [[ -f "$ENV_FILE" ]]; then
    file="$ENV_FILE"
  elif [[ -f "$LOCAL_ENV" ]]; then
    file="$LOCAL_ENV"
  else
    echo "missing env file: $ENV_FILE or $LOCAL_ENV" >&2
    return 1
  fi
  chmod 600 "$file"
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
}

secure_runtime_paths() {
  install -d -m 700 "$RUN_DIR" "$LOG_DIR" "$PID_DIR" "$RUN_DIR/bin" "$ETC_DIR"
  find "$LOG_DIR" -maxdepth 1 -type f -name '*.log*' -exec chmod 600 {} + 2>/dev/null || true
  find "$PID_DIR" -maxdepth 1 -type f -name '*.pid' -exec chmod 600 {} + 2>/dev/null || true
}

# assistant.yaml DataSource is "${DB_ASSISTANT}". Older env files only set
# DB_CONTENT; derive the DSN by swapping the schema name, keep user/query.
ensure_assistant_db_env() {
  if [[ -z "${DB_ASSISTANT:-}" && -n "${DB_CONTENT:-}" ]]; then
    export DB_ASSISTANT="${DB_CONTENT/xbh_content/xbh_assistant}"
  fi
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
  docker exec -i -e REDISCLI_AUTH="$pass" "$REDIS_CONTAINER" \
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
  mkdir -p "$ETC_DIR"
  (
    cd "$BACKEND"
    find app -path '*/etc/*.yaml' -print0
  ) | while IFS= read -r -d '' rel; do
    mkdir -p "$ETC_DIR/$(dirname "$rel")"
    # Dev copies bind everything to loopback: RPC ListenOn (etcd registration
    # must stay reachable from the host gateway), the gateway REST listener
    # (RestConf) and the DevServer metrics/pprof HTTP endpoints, which would
    # otherwise listen on all interfaces. Sub-repo yaml files are never
    # modified.
    sed -e 's/ListenOn: 0\.0\.0\.0:/ListenOn: 127.0.0.1:/' \
        -e '/^DevServer:/,/^[^ ]/{s/^\([[:space:]]*\)Host: 0\.0\.0\.0/\1Host: 127.0.0.1/}' \
        -e '/^RestConf:/,/^[^ ]/{s/^\([[:space:]]*\)Host: 0\.0\.0\.0/\1Host: 127.0.0.1/}' \
        "$BACKEND/$rel" >"$ETC_DIR/$rel"
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
  docker exec -i -e MYSQL_PWD="$pass" xbh-mysql \
    mysql -uroot --default-character-set=utf8mb4 "$@"
}

# Same empty-volume constraint as schema SQL. Seed the local test user on
# every middleware-up so existing MySQL volumes get admin/123456.
apply_dev_user() {
  local sql="$ROOT/deploy/dev/seed_dev_user.sql"
  echo "seeding local test user admin"
  mysql_root <"$sql"
}

# The black-box e2e suite probes MySQL rows through a dedicated read-only
# account (deploy/dev/e2e/dbprobe.py). Nothing in the schema SQL creates it,
# so seed it here on every middleware-up; ALTER USER keeps an already-seeded
# volume self-healing. Values must be single-quote-safe.
# The same default account (xbh) is the app DSN user: it already has ALL on
# the other xbh_* schemas from init, but xbh_assistant landed later and
# needs ALL so assistant-rpc can write memory/watch.
apply_e2e_db_grants() {
  local user="${E2E_MYSQL_USER:-xbh}"
  local pass="${E2E_MYSQL_PASSWORD:-xbhdev}"
  echo "seeding e2e db account ${user}"
  mysql_root <<SQL
CREATE USER IF NOT EXISTS '${user}'@'%';
ALTER USER '${user}'@'%' IDENTIFIED BY '${pass}';
GRANT SELECT ON xbh_content.* TO '${user}'@'%';
GRANT SELECT ON xbh_user.* TO '${user}'@'%';
GRANT SELECT ON xbh_interaction.* TO '${user}'@'%';
GRANT SELECT ON xbh_media.* TO '${user}'@'%';
GRANT SELECT ON xbh_message.* TO '${user}'@'%';
GRANT SELECT ON xbh_feed.* TO '${user}'@'%';
CREATE DATABASE IF NOT EXISTS xbh_assistant DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON xbh_assistant.* TO '${user}'@'%';
FLUSH PRIVILEGES;
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
    mysql_root <"$sql"
  done
}

require_apps_stopped_for_patches() {
  local name pidfile pid running=()
  while IFS= read -r name; do
    [[ "$name" == "log-maintainer" ]] && continue
    pidfile="$PID_DIR/$name.pid"
    [[ -f "$pidfile" ]] || continue
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      running+=("$name:$pid")
    fi
  done < <(all_app_names)
  if [[ ${#running[@]} -gt 0 ]]; then
    echo "refusing schema patches while app processes are running: ${running[*]}" >&2
    echo "run 'just app-down' before 'just middleware-up'" >&2
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
  python3 "$script" "${files[@]}" | mysql_root
  mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN 1001 AND 1300;" </dev/null \
    | awk '{print "eval corpus posts 1001-1300: "$1}'
  mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN 2001 AND 4000;" </dev/null \
    | awk '{print "eval corpus posts 2001-4000: "$1}'
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
  load_env
  local corpus_n es_n
  corpus_n="$(mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN 1001 AND 4000 AND status = 1;" </dev/null | tr -d '[:space:]')"
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
    cd "$BACKEND"
    go run ./app/search/mq/cmd/rebuild -f "$ETC_DIR/app/search/mq/etc/search-consumer.yaml"
  )
}

start_svc() {
  local name="$1" workdir="$2" bin="$3"
  shift 3
  local pidfile="$PID_DIR/$name.pid"
  local logfile="$LOG_DIR/$name.log"
  local bindir="$RUN_DIR/bin"
  local executable="$bindir/$name"
  secure_runtime_paths
  touch "$logfile"
  chmod 600 "$logfile"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "already running: $name pid=$(cat "$pidfile")"
    return 0
  fi
  echo "building $name"
  (
    cd "$workdir"
    go build -o "$executable.tmp" "$bin"
  ) >>"$logfile" 2>&1
  mv -f "$executable.tmp" "$executable"
  echo "starting $name"
  (
    cd "$workdir"
    setsid "$executable" "$@" >>"$logfile" 2>&1 </dev/null &
    echo $! >"$pidfile"
    chmod 600 "$pidfile"
  )
  sleep 0.1
  if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "$name exited during startup; see $logfile" >&2
    return 1
  fi
}

start_log_maintainer() {
  local pidfile="$PID_DIR/log-maintainer.pid"
  secure_runtime_paths
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    return 0
  fi
  setsid python3 "$ROOT/deploy/dev/log_maintainer.py" "$LOG_DIR" \
    --max-bytes "$LOG_MAX_BYTES" --interval "$LOG_ROTATE_INTERVAL_SECONDS" \
    >/dev/null 2>&1 </dev/null &
  echo $! >"$pidfile"
  chmod 600 "$pidfile"
  sleep 0.1
  if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "log maintainer exited during startup" >&2
    return 1
  fi
}

start_row() {
  local row="$1"
  IFS='|' read -r name workdir bin flag conf <<<"$row"
  start_svc "$name" "$workdir" "$bin" "$flag" "$conf"
}

stop_tree() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null && ! kill -0 -- "-$pid" 2>/dev/null; then
    return 0
  fi
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$pid" 2>/dev/null && ! kill -0 -- "-$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.2
  done
  kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
}

stop_svc() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  if [[ ! -f "$pidfile" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pidfile")"
  if [[ -n "$pid" ]]; then
    echo "stopping $name pid=$pid"
    stop_tree "$pid"
  fi
  rm -f "$pidfile"
}

all_app_names() {
  local row name
  for row in "${RPC_SERVICES[@]}" "${MQ_SERVICES[@]}"; do
    IFS='|' read -r name _ <<<"$row"
    printf '%s\n' "$name"
  done
  printf '%s\n' gateway frontend log-maintainer
}

middleware_up() {
  load_env
  secure_runtime_paths
  require_apps_stopped_for_patches
  require_compose_version
  echo "starting middleware containers"
  compose up -d
  wait_port 127.0.0.1 3306 90 mysql
  apply_dev_user
  apply_sql_patches
  apply_e2e_db_grants
  apply_eval_corpus
  wait_port 127.0.0.1 6379 60 redis
  wait_port 127.0.0.1 2379 60 etcd
  wait_port 127.0.0.1 9200 90 elasticsearch
  wait_port 127.0.0.1 9876 90 rocketmq-namesrv
  wait_port 127.0.0.1 10911 180 rocketmq-broker
  wait_topics 180
  wait_port 127.0.0.1 8123 60 clickhouse
  apply_analytics_schema
  wait_http "http://127.0.0.1:3100/ready" 90 loki
  wait_port 127.0.0.1 9333 60 seaweedfs-master || true
}

middleware_down() {
  # Containers only; the :3002 proxy is app-layer and belongs to proxy_down.
  echo "stopping middleware containers (volumes kept)"
  compose stop
}

# Opt-in algorithm services live behind the compose profile "algorithm" so
# middleware-only stacks skip model downloads and inference ports. recommend-rpc
# already dials 127.0.0.1:9025 (ONLINE_INFER_ENDPOINT); until these containers
# are up it degrades to rule-based ranking.
algorithm_up() {
  load_env
  require_compose_version
  echo "starting algorithm containers (embedding-service, online-infer)"
  COMPOSE_PROFILES=algorithm compose up -d
  wait_port 127.0.0.1 9025 300 online-infer
}

algorithm_down() {
  echo "stopping algorithm containers"
  COMPOSE_PROFILES=algorithm compose stop online-infer embedding-service || true
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
  mkdir -p "$FRONTEND/web"
  if [[ "$(readlink -f "$dst" 2>/dev/null)" != "$src" ]]; then
    rm -f "$dst"
    ln -s "$src" "$dst"
  fi
  [[ -e "$dst/canvaskit.js" ]]
}

proxy_up() {
  if docker inspect "$PROXY_NAME" >/dev/null 2>&1; then
    docker rm -f "$PROXY_NAME" >/dev/null
  fi
  echo "starting $PROXY_NAME on :$ENTRY_PORT"
  docker run -d --name "$PROXY_NAME" --network host --restart unless-stopped \
    -v "$PROXY_CONF:/etc/nginx/nginx.conf:ro" \
    nginx:stable-alpine >/dev/null
}

proxy_down() {
  if docker inspect "$PROXY_NAME" >/dev/null 2>&1; then
    echo "stopping $PROXY_NAME"
    docker rm -f "$PROXY_NAME" >/dev/null
  fi
}

frontend_up() {
  local pidfile="$PID_DIR/frontend.pid"
  local logfile="$LOG_DIR/frontend.log"
  secure_runtime_paths
  touch "$logfile"
  chmod 600 "$logfile"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "already running: frontend pid=$(cat "$pidfile")"
    return 0
  fi

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
    if ! (
      cd "$FRONTEND"
      env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
        -u ALL_PROXY -u all_proxy \
        flutter build web --release -t lib/main.dart \
        --no-web-resources-cdn \
        --dart-define=FLUTTER_WEB_CANVASKIT_URL="${canvaskit_url:-/canvaskit/}"
    ) >>"$logfile" 2>&1; then
      if [[ -f "$bundle/index.html" ]]; then
        echo "warning: frontend build failed, serving stale bundle" >&2
        rm -f "$RUN_DIR/front-build.stamp"
      else
        echo "frontend build failed and no bundle exists; see $logfile" >&2
        return 1
      fi
    else
      touch "$RUN_DIR/front-build.stamp"
    fi
  else
    echo "frontend bundle up to date; set FORCE_FRONT_BUILD=1 to rebuild"
  fi

  echo "starting frontend on :$FRONT_PORT (static release bundle)"
  (
    cd "$FRONTEND"
    setsid python3 "$ROOT/deploy/dev/serve_release.py" "$FRONT_PORT" "$bundle" \
      >>"$logfile" 2>&1 </dev/null &
    echo $! >"$pidfile"
    chmod 600 "$pidfile"
  )
}

# True when no tracked frontend source is newer than the last build stamp.
front_bundle_fresh() {
  local stamp="$RUN_DIR/front-build.stamp"
  [[ -f "$stamp" ]] || return 1
  local changed roots=("$FRONTEND/lib" "$FRONTEND/web" "$FRONTEND/pubspec.yaml" "$FRONTEND/pubspec.lock")
  [[ -d "$FRONTEND/assets" ]] && roots+=("$FRONTEND/assets")
  changed="$(find "${roots[@]}" \
    -type f -newer "$stamp" -print -quit 2>/dev/null)"
  [[ -z "$changed" ]]
}

app_up() {
  load_env
  ensure_assistant_db_env
  secure_runtime_paths
  wipe_legacy_assistant_redis
  prepare_etc
  start_log_maintainer
  local row
  for row in "${RPC_SERVICES[@]}"; do
    start_row "$row"
  done
  wait_port 127.0.0.1 9090 240 user-rpc
  wait_topics 180
  for row in "${MQ_SERVICES[@]}"; do
    start_row "$row"
  done
  start_svc gateway "$BACKEND" ./app/gateway -f "$ETC_DIR/app/gateway/etc/gateway.yaml"
  frontend_up
  proxy_up
  wait_port 127.0.0.1 "$GATEWAY_PORT" 240 gateway
  wait_port 127.0.0.1 "$FRONT_PORT" 240 frontend
  maybe_rebuild_search
  echo "entry http://127.0.0.1:$ENTRY_PORT/  (page=$(http_code "http://127.0.0.1:$ENTRY_PORT/") api=$(http_code "http://127.0.0.1:$ENTRY_PORT/api/v1/"))"
}

app_down() {
  local name
  while IFS= read -r name; do
    stop_svc "$name"
  done < <(all_app_names | tac)
  proxy_down
  if port_open 127.0.0.1 "$FRONT_PORT"; then
    fuser -k "$FRONT_PORT/tcp" >/dev/null 2>&1 || true
  fi
  if port_open 127.0.0.1 "$GATEWAY_PORT"; then
    fuser -k "$GATEWAY_PORT/tcp" >/dev/null 2>&1 || true
  fi
}

pid_state() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"
  if [[ ! -f "$pidfile" ]]; then
    printf '%-18s %s\n' "$name" "no-pid"
    return
  fi
  local pid
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    printf '%-18s alive pid=%s\n' "$name" "$pid"
  else
    printf '%-18s DEAD  pid=%s\n' "$name" "$pid"
  fi
}

stack_status() {
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
  printf ':9136 agent %s\n' "$(http_code "http://127.0.0.1:9136/metrics")"
}
