# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Loading the local secrets file and normalizing the values derived from it.

# Prints the env file to use: /tmp/xbh-dev.env first, then deploy/dev/.env.
resolve_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    printf '%s\n' "$ENV_FILE"
  elif [[ -f "$LOCAL_ENV" ]]; then
    printf '%s\n' "$LOCAL_ENV"
  else
    echo "missing env file: $ENV_FILE or $LOCAL_ENV" >&2
    return 1
  fi
}

# Validates a port held in the variable named $1 and rewrites it in canonical
# decimal form (so "03002" and "3002" render identically everywhere).
normalize_port_var() {
  local name="$1" label="${2:-$1}" port="${!1:-}"
  if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] ||
    ((10#$port < 1 || 10#$port > 65535)); then
    echo "invalid $label: $port" >&2
    return 1
  fi
  printf -v "$name" '%s' "$((10#$port))"
}

# Entry, frontend and gateway ports are all user-overridable; check them together.
normalize_stack_ports() {
  local name
  for name in ENTRY_PORT FRONT_PORT GATEWAY_PORT; do
    normalize_port_var "$name" || return $?
  done
}

# The agent metrics port feeds config rendering, readiness and status alike.
normalize_assistant_agent_metrics_port() {
  normalize_port_var ASSISTANT_AGENT_METRICS_PORT "assistant-agent metrics port"
}

# Sources the env file with auto-export, then fills defaults for optional
# assistant settings so every service config resolves its ${VAR} references.
load_env() {
  local file env_status
  file="$(resolve_env_file)" || return $?
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

  # Optional primary-route settings referenced by the agent config.
  export ASSISTANT_LLM_MODEL_SMALL="${ASSISTANT_LLM_MODEL_SMALL:-}"
  export TAVILY_ENDPOINT="${TAVILY_ENDPOINT:-}"
  export ASSISTANT_LLM_REVIEW_MODEL="${ASSISTANT_LLM_REVIEW_MODEL:-}"
  export ASSISTANT_LLM_CACHE_READ_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_CACHE_READ_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_CACHE_WRITE_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_CACHE_WRITE_COST_PER_MILLION_TOKENS:-0}"
  export ASSISTANT_LLM_REASONING_COST_PER_MILLION_TOKENS="${ASSISTANT_LLM_REASONING_COST_PER_MILLION_TOKENS:-0}"

  # Fallback route is off by default; its fields still need defined values.
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

  # Review cascade ranker: the stub sidecar (just infer-up) on loopback. When it
  # is not running the worker degrades every candidate to human review.
  export MODERATION_INFER_ADDRESS="${MODERATION_INFER_ADDRESS:-127.0.0.1:$MODERATION_INFER_PORT}"
  # Dev-only: lets e2e drive deterministic ranker scores with text markers.
  export MODERATION_FIXTURE_ENABLED="${MODERATION_FIXTURE_ENABLED:-1}"

  ensure_assistant_db_env || return $?
  validate_dev_db_env
}

# Derives the per-schema DSNs of the newer services. assistant.yaml reads
# "${DB_ASSISTANT}"; ad and review services read "${DB_AD}" / "${DB_REVIEW}".
# Older env files only set DB_CONTENT, so swap the schema name and keep the
# credentials and query string.
ensure_assistant_db_env() {
  local schema key
  for schema in assistant ad review; do
    key="DB_${schema^^}"
    if [[ -z "${!key:-}" && -n "${DB_CONTENT:-}" ]]; then
      export "$key=${DB_CONTENT/xbh_content/xbh_$schema}"
    fi
  done
}
