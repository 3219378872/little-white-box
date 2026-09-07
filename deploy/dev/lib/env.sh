# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

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
