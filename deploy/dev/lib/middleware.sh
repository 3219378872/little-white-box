# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Docker middleware from the backend compose file: start/stop, readiness, and
# the idempotent schema, account and data seeding that existing volumes need.

# The opt-in algorithm/training services read model-registry credentials
# without defaults; pass them through (empty when unset) so compose does not
# warn on every middleware command.
compose() {
  MODEL_S3_ACCESS_KEY="${MODEL_S3_ACCESS_KEY:-}" \
  MODEL_S3_SECRET_KEY="${MODEL_S3_SECRET_KEY:-}" \
  docker compose -p "$COMPOSE_PROJECT" \
    -f "$COMPOSE_FILE" \
    -f "$OVERRIDE" \
    --project-directory "$BACKEND/deploy" \
    "$@"
}

# Runs redis-cli against REDIS_HOST (first endpoint, scheme/db stripped), via
# the local binary or, when it is missing, inside the Redis container.
redis_command() {
  local endpoint host port pass
  endpoint="${REDIS_HOST:-127.0.0.1:$REDIS_PORT}"
  endpoint="${endpoint%%,*}"
  endpoint="${endpoint#redis://}"
  endpoint="${endpoint#rediss://}"
  endpoint="${endpoint%%/*}"
  host="${endpoint%:*}"
  port="${endpoint##*:}"
  if [[ "$host" == "$endpoint" ]]; then
    port="$REDIS_PORT"
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

# Old sync Assistant stored Redis sessions under assistant:v2*. The v3 runtime
# only uses Redis for run-event notify keys, so wiping the legacy namespace on
# every app-up is idempotent and does not touch the MySQL marker.
wipe_legacy_assistant_redis() {
  local script count_script deleted remaining
  # Server-side SCAN loops: one UNLINKs every match, the other only counts what
  # is left so the wipe can be verified independently.
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

# The broker container runs the backend's init-topics.sh, so its TOPICS array
# is the only list worth waiting for; a copy here drifts when the backend drops
# a topic and a fresh volume then never becomes ready.
rocketmq_bootstrap_topics() {
  local script="$BACKEND/deploy/rocketmq/init-topics.sh"
  local line state=0 word
  local -a words topics=()
  if [[ ! -f "$script" ]]; then
    echo "missing RocketMQ bootstrap script: $script" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    if [[ "$state" -eq 0 ]]; then
      [[ "$line" =~ ^[[:space:]]*TOPICS=\([[:space:]]*$ ]] && state=1
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*\)[[:space:]]*$ ]]; then
      state=2
      break
    fi
    read -r -a words <<<"$line"
    for word in "${words[@]}"; do
      if [[ ! "$word" =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "unsupported RocketMQ topic entry in $script: $word" >&2
        return 1
      fi
      topics+=("$word")
    done
  done <"$script"
  if [[ "$state" -ne 2 || "${#topics[@]}" -eq 0 ]]; then
    echo "no TOPICS=( ... ) list found in $script" >&2
    return 1
  fi
  printf '%s\n' "${topics[@]}"
}

# Polls the broker until every bootstrap topic exists (every 2s, up to
# SECONDS). Consumers fail to subscribe to missing topics, so app-up waits.
wait_topics() {
  local seconds="${1:-180}"
  local -a needed
  local i list ok t topics
  topics="$(rocketmq_bootstrap_topics)" || return $?
  mapfile -t needed <<<"$topics"
  for ((i = 0; i < seconds; i += 2)); do
    # rocketmq-namesrv:9876 is the compose-network address seen from inside.
    list="$(docker exec "$ROCKETMQ_BROKER_CONTAINER" sh -c 'sh mqadmin topicList -n rocketmq-namesrv:9876' 2>/dev/null || true)"
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
  docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --multiquery <"$sql"
}

# The black-box suite reads xbh_analytics through the same read-only e2e
# identity as MySQL instead of the unrestricted ClickHouse `default` user.
# Only a SHA-256 hex digest reaches SQL, so the password is never interpolated
# as a literal or printed; revoke-first drops grants from older stack versions.
apply_clickhouse_e2e_grants() {
  validate_dev_db_env || return 1
  local e2e_user="$E2E_MYSQL_USER" digest
  digest="$(printf '%s' "$E2E_MYSQL_PASSWORD" | python3 -c \
    'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')" || return 1
  if [[ ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
    echo "failed to hash ClickHouse e2e password" >&2
    return 1
  fi
  echo "seeding read-only ClickHouse e2e account (${e2e_user})"
  docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --multiquery <<SQL
CREATE USER IF NOT EXISTS \`${e2e_user}\` IDENTIFIED WITH sha256_hash BY '${digest}';
ALTER USER \`${e2e_user}\` IDENTIFIED WITH sha256_hash BY '${digest}';
REVOKE ALL ON *.* FROM \`${e2e_user}\`;
GRANT SELECT ON xbh_analytics.* TO \`${e2e_user}\`;
SQL
}

# mysql client as root inside the MySQL container. The password travels in
# the environment (MYSQL_PWD), never on a command line visible in ps.
mysql_root() {
  local pass="${MYSQL_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD_DEFAULT}"
  MYSQL_PWD="$pass" docker exec -i -e MYSQL_PWD "$MYSQL_CONTAINER" \
    mysql -uroot --default-character-set=utf8mb4 "$@"
}

# Hex-encodes a value so SQL can carry it as X'..' data instead of a literal.
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
CREATE DATABASE IF NOT EXISTS xbh_ad DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS xbh_review DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

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
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_ad.* TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.review_snapshot TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.review_task TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.review_stage_result TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.review_decision TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.verdict_cache TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.approved_media TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.review_seed TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.reviewer TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.event_outbox TO '${app_user}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON xbh_review.idempotency TO '${app_user}'@'%';
GRANT SELECT, INSERT ON xbh_review.audit_log TO '${app_user}'@'%';

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
GRANT SELECT ON xbh_ad.* TO '${e2e_user}'@'%';
GRANT SELECT ON xbh_review.* TO '${e2e_user}'@'%';

DROP USER IF EXISTS 'xbh'@'%';
DROP USER IF EXISTS 'xbh'@'localhost';
SQL
}

# Schemas added after the initdb.d baseline (xbh_ad, xbh_review) never run on
# existing volumes. Their files are CREATE ... IF NOT EXISTS only, so replaying
# them is idempotent; table-level review grants need the tables to exist first.
apply_new_schema_baselines() {
  local schema sql
  for schema in xbh_ad xbh_review; do
    sql="$BACKEND/deploy/sql/$schema.sql"
    [[ -f "$sql" ]] || continue
    echo "applying $schema baseline schema"
    mysql_root <"$sql" || return $?
  done
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
    # The v3 runtime patch resets legacy Assistant tables; only allow it on a
    # volume that is already migrated or holds no Assistant data.
    if [[ "${sql##*/}" == 20260829_assistant_runtime_v3.sql ]]; then
      require_safe_assistant_baseline || return $?
    fi
    mysql_root <"$sql" || return $?
  done
}

# Succeeds when the assistant_runtime_v3 marker is present, or when every
# existing xbh_assistant table is empty; refuses otherwise.
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

# Appends KEY to the caller's `running` array unless its `seen` list already
# has it, so one process reported by several probes is listed once.
note_running_app() {
  if [[ "$seen" != *" $1 "* ]]; then
    running+=("$1")
    seen+="$1 "
  fi
}

# Schema patches and middleware shutdown must not race live app processes.
# Collects every running app (by pidfile, orphaned group or process scan) and
# refuses OPERATION while any is found; log-maintainer is exempt.
require_apps_stopped_for_patches() {
  local operation="${1:-schema patches}"
  local recovery="${2:-run 'just app-down' before 'just middleware-up'}"
  local name pidfile pid seen=" "
  local -a running=()

  # Pass 1: recorded pidfiles (live leader or orphaned owned group).
  while IFS= read -r name; do
    [[ "$name" == "log-maintainer" ]] && continue
    pidfile="$PID_DIR/$name.pid"
    if pid="$(validated_service_pid "$name" "$pidfile")"; then
      note_running_app "$name:$pid"
    elif pid="$(read_service_pidfile "$pidfile" 2>/dev/null)" &&
      managed_process_group_matches "$name" "$pid" "$pidfile"; then
      note_running_app "$name:group-$pid"
    fi
  done < <(all_app_names)

  # Pass 2: processes that lost their pidfile but still match a service identity.
  while IFS= read -r name; do
    [[ "$name" == "log-maintainer" ]] && continue
    while IFS= read -r pid; do
      [[ -n "$pid" ]] || continue
      note_running_app "$name:$pid"
    done < <(service_process_pids "$name")
  done < <(all_app_names)

  # Pass 3: anything at all on the gateway port.
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
  # Report what landed per id range.
  local range first last
  for range in "$EVAL_CORPUS_IDS" "$BULK_CORPUS_IDS"; do
    first="${range%-*}"
    last="${range#*-}"
    mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN $first AND $last;" </dev/null \
      | awk -v range="$range" '{print "eval corpus posts " range ": "$1}' || return $?
  done
}

# Prints the document count from an ES _count URL, or 0 when unreachable.
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
  corpus_n="$(mysql_root -N -e "SELECT COUNT(*) FROM xbh_content.post WHERE id BETWEEN ${EVAL_CORPUS_IDS%-*} AND ${BULK_CORPUS_IDS#*-} AND status = 1;" </dev/null | tr -d '[:space:]')" || return $?
  es_n="$(search_doc_count "$SEARCH_INDEX_URL/_count" | tr -d '[:space:]')"
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

# search-rpc panics at startup unless its index (or alias) already exists, and
# only search-mq (EnsureIndex) or the rebuild tool creates it. On a fresh
# Elasticsearch volume app-up must therefore wait for search-mq first.
wait_search_index() {
  wait_http "$SEARCH_INDEX_URL" "${1:-120}" search-index
}

# Starts the middleware and brings existing volumes up to date. Apps must be
# down because schema patches and account grants change what they rely on.
middleware_up_locked() {
  load_env || return $?
  secure_runtime_paths || return $?
  require_apps_stopped_for_patches || return $?
  require_compose_version || return $?
  echo "starting middleware containers"
  compose up -d || return $?

  # MySQL: patches and seeds exec into the container right away; the published
  # port opens before the server does, so wait for the container healthcheck.
  # Order: test user, patches, late schema baselines, then grants (table-level
  # review grants need those tables), then eval data.
  wait_healthy "$MYSQL_CONTAINER" 120 mysql || return $?
  apply_dev_user || return $?
  apply_sql_patches || return $?
  apply_new_schema_baselines || return $?
  apply_dev_db_grants || return $?
  apply_eval_corpus || return $?

  # Plain TCP services the apps connect to on startup.
  wait_port 127.0.0.1 "$REDIS_PORT" 60 redis || return $?
  wait_port 127.0.0.1 "$ETCD_PORT" 60 etcd || return $?
  wait_port 127.0.0.1 "$ELASTICSEARCH_PORT" 90 elasticsearch || return $?
  wait_port 127.0.0.1 "$ROCKETMQ_NAMESRV_PORT" 90 rocketmq-namesrv || return $?
  wait_port 127.0.0.1 "$ROCKETMQ_BROKER_PORT" 180 rocketmq-broker || return $?
  wait_topics 180 || return $?

  # ClickHouse: schema first, then the read-only e2e account that reads it.
  wait_healthy "$CLICKHOUSE_CONTAINER" 120 clickhouse || return $?
  apply_analytics_schema || return $?
  apply_clickhouse_e2e_grants || return $?

  wait_http "http://127.0.0.1:$LOKI_PORT/ready" 90 loki || return $?
  # SeaweedFS is best-effort: only media uploads depend on it.
  wait_port 127.0.0.1 "$SEAWEEDFS_MASTER_PORT" 60 seaweedfs-master || true
}

middleware_up() {
  with_app_lifecycle_lock exclusive middleware_up_locked
}

# Stops containers only (volumes kept); refuses while apps still use them.
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
# already dials 127.0.0.1:$ONLINE_INFER_PORT (ONLINE_INFER_ENDPOINT); until these containers
# are up it degrades to rule-based ranking.
algorithm_up_locked() {
  load_env || return $?
  require_compose_version || return $?
  echo "starting algorithm containers (embedding-service, online-infer, moderation-infer)"
  COMPOSE_PROFILES=algorithm compose up -d || return $?
  # First start downloads model weights, hence the long online-infer timeout.
  wait_port 127.0.0.1 "$ONLINE_INFER_PORT" 300 online-infer || return $?
  wait_port 127.0.0.1 "$MODERATION_INFER_PORT" 120 moderation-infer || return $?
}

algorithm_up() {
  with_app_lifecycle_lock exclusive algorithm_up_locked
}

# Stops (never removes) the algorithm containers so model weights stay cached.
algorithm_down_locked() {
  echo "stopping algorithm containers"
  COMPOSE_PROFILES=algorithm compose stop online-infer embedding-service moderation-infer || return $?
}

algorithm_down() {
  with_app_lifecycle_lock exclusive algorithm_down_locked
}
