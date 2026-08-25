# Shared helpers for the workspace justfile. Sourced, not executed.
# shellcheck shell=bash

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
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
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
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 "$url" || echo 000)"
    if [[ "$code" == "200" ]]; then
      echo "ready: $label"
      return 0
    fi
    sleep 1
  done
  echo "timeout waiting for $label" >&2
  return 1
}

http_code() {
  curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$1" || echo "000"
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
apply_e2e_db_grants() {
  local user="${E2E_MYSQL_USER:-xbh}"
  local pass="${E2E_MYSQL_PASSWORD:-xbhdev}"
  echo "seeding e2e read-only db account ${user}"
  mysql_root <<SQL
CREATE USER IF NOT EXISTS '${user}'@'%';
ALTER USER '${user}'@'%' IDENTIFIED BY '${pass}';
GRANT SELECT ON xbh_content.* TO '${user}'@'%';
GRANT SELECT ON xbh_user.* TO '${user}'@'%';
GRANT SELECT ON xbh_interaction.* TO '${user}'@'%';
GRANT SELECT ON xbh_media.* TO '${user}'@'%';
GRANT SELECT ON xbh_message.* TO '${user}'@'%';
GRANT SELECT ON xbh_feed.* TO '${user}'@'%';
FLUSH PRIVILEGES;
SQL
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
  mkdir -p "$PID_DIR" "$LOG_DIR"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "already running: $name pid=$(cat "$pidfile")"
    return 0
  fi
  echo "starting $name"
  (
    cd "$workdir"
    setsid go run "$bin" "$@" >"$logfile" 2>&1 </dev/null &
    echo $! >"$pidfile"
  )
}

start_row() {
  local row="$1"
  IFS='|' read -r name workdir bin flag conf <<<"$row"
  start_svc "$name" "$workdir" "$bin" "$flag" "$conf"
}

stop_tree() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || return 0
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
  printf '%s\n' gateway frontend
}

middleware_up() {
  require_compose_version
  echo "starting middleware containers"
  compose up -d
  wait_port 127.0.0.1 3306 90 mysql
  apply_dev_user
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
  echo "stopping middleware containers (volumes kept)"
  compose stop
  docker stop "$PROXY_NAME" >/dev/null 2>&1 || true
}

# Local copy of the Flutter web engine assets (CanvasKit/Skwasm). Serving them
# from the dev origin keeps browsers behind restrictive networks from needing
# gstatic.com, which otherwise hangs engine init with a blank page.
resolve_canvaskit_dir() {
  if [[ -z "$CANVASKIT_DIR" ]]; then
    local fl sdk
    fl="$(command -v flutter 2>/dev/null || true)"
    if [[ -n "$fl" && -f "$fl" ]]; then
      sdk="$(cd "$(dirname "$(readlink -f "$fl")")/.." && pwd)"
      CANVASKIT_DIR="$sdk/bin/cache/flutter_web_sdk/canvaskit"
    fi
  fi
  [[ -n "$CANVASKIT_DIR" && -d "$CANVASKIT_DIR" ]]
}

proxy_up() {
  if docker inspect "$PROXY_NAME" >/dev/null 2>&1; then
    docker rm -f "$PROXY_NAME" >/dev/null
  fi
  echo "starting $PROXY_NAME on :$ENTRY_PORT"
  local mounts=()
  if resolve_canvaskit_dir; then
    mounts+=(-v "$CANVASKIT_DIR:/var/www/canvaskit:ro")
  else
    echo "warning: flutter_web_sdk canvaskit dir not found, /canvaskit/ disabled" >&2
  fi
  docker run -d --name "$PROXY_NAME" --network host --restart unless-stopped \
    "${mounts[@]}" \
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
  mkdir -p "$PID_DIR" "$LOG_DIR"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "already running: frontend pid=$(cat "$pidfile")"
    return 0
  fi
  echo "starting frontend on :$FRONT_PORT"
  (
    cd "$FRONTEND"
    # Proxy env vars break the DWDS debug websocket (RunRequest never reaches
    # the browser -> blank page). The dev server only talks to localhost.
    setsid env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
      -u ALL_PROXY -u all_proxy FLUTTER_WEB_CANVASKIT_URL=/canvaskit/ \
      make dev-real HOST=127.0.0.1 PORT="$FRONT_PORT" \
      >"$logfile" 2>&1 </dev/null &
    echo $! >"$pidfile"
  )
}

app_up() {
  load_env
  prepare_etc
  mkdir -p "$PID_DIR" "$LOG_DIR"
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
}
