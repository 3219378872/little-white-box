# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Rendering backend etc/*.yaml into ETC_DIR copies for the local stack. The
# sub-repo files are only ever read, never modified.

# True when FILE's Prometheus block listens on PORT (the agent readiness check
# looks for its metrics listener there).
assistant_agent_metrics_config_matches() {
  local file="$1" port="$2"
  awk -v expected_port="$port" '
    /^Prometheus:$/ { inside = 1; next }
    inside && /^[^[:space:]]/ { inside = 0 }
    inside && $1 == "Port:" && $2 == expected_port { port_matches = 1 }
    END { exit !port_matches }
  ' "$file"
}

# Renders one backend config REL (relative to BACKEND) into ETC_DIR.
render_service_config() {
  local rel="$1" target="$ETC_DIR/$1"
  local media_url="${MEDIA_PUBLIC_BASE_URL:-http://127.0.0.1:$ENTRY_PORT/xbh-media}"

  # Bind everything to loopback: RPC ListenOn (etcd registration must stay
  # reachable from the host gateway), the gateway REST listener (RestConf) and
  # the DevServer diagnostics endpoints.
  local -a sed_args=(
    -e 's/ListenOn: 0\.0\.0\.0:/ListenOn: 127.0.0.1:/'
    -e '/^DevServer:/,/^[^ ]/{s/^\([[:space:]]*\)Host: 0\.0\.0\.0/\1Host: 127.0.0.1/}'
    -e '/^RestConf:/,/^[^ ]/{s/^\([[:space:]]*\)Host: 0\.0\.0\.0/\1Host: 127.0.0.1/}'
  )
  # The agent Prometheus port is configurable here and in readiness/status as
  # one value.
  if [[ "$rel" == "app/assistant/worker/etc/agent.yaml" ]]; then
    sed_args+=(
      -e "/^Prometheus:/,/^[^ ]/{s/^\\([[:space:]]*\\)Port: [0-9][0-9]*/\\1Port: $ASSISTANT_AGENT_METRICS_PORT/}"
    )
  fi
  mkdir -p "$(dirname "$target")" || return $?
  sed "${sed_args[@]}" "$BACKEND/$rel" >"$target" || return $?

  # Per-file follow-ups that need a structural (not regex) edit.
  case "$rel" in
    app/gateway/etc/gateway.yaml)
      python3 "$ROOT/deploy/dev/render_ports.py" gateway "$target" "$target" \
        "$ENTRY_PORT" "$FRONT_PORT" "$GATEWAY_PORT" || return $?
      ;;
    app/media/rpc/etc/media.yaml | app/ad/rpc/etc/ad.yaml | app/ad/mq/etc/ad-consumer.yaml)
      # Media links handed to browsers must go through the same-origin proxy.
      python3 "$ROOT/deploy/dev/render_ports.py" media "$target" "$target" "$media_url" || return $?
      ;;
    app/assistant/worker/etc/agent.yaml)
      if ! assistant_agent_metrics_config_matches "$target" "$ASSISTANT_AGENT_METRICS_PORT"; then
        echo "failed to configure assistant-agent Prometheus endpoint" >&2
        return 1
      fi
      ;;
  esac
}

# Renders every backend etc/*.yaml into ETC_DIR with the current ports.
prepare_etc() {
  normalize_stack_ports || return $?
  normalize_assistant_agent_metrics_port || return $?
  mkdir -p "$ETC_DIR" || return $?
  # A pipeline (not process substitution) so pipefail reports a missing
  # BACKEND or a failed find instead of rendering nothing.
  (
    cd "$BACKEND" || exit $?
    find app -path '*/etc/*.yaml' -print0
  ) | while IFS= read -r -d '' rel; do
    render_service_config "$rel" || exit $?
  done
}
