# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

compose() {
  MINIO_ROOT_USER="${MINIO_ROOT_USER:-admin}" \
  MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-Xbh@Minio2024!}" \
  docker compose -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_FILE" \
    -f "$OVERRIDE" \
    --project-directory "$BACKEND/deploy" \
    "$@"
}

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
