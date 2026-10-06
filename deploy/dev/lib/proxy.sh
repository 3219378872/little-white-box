# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Same-origin entry proxy: an nginx container on the host network that serves
# :ENTRY_PORT and routes / to the frontend, /api/ to the gateway and
# /xbh-media/ to SeaweedFS. Its config is rendered from deploy/dev/proxy.conf.

# Decides whether the proxy also listens on [::]. Returns 0 (yes), 1 (no) or
# 2 for an invalid PROXY_IPV6 value.
proxy_ipv6_enabled() {
  case "$PROXY_IPV6" in
    1) return 0 ;;
    0) return 1 ;;
    auto)
      # Only a Linux procfs can prove IPv6 is absent; keep it everywhere else.
      if [[ -d /proc/net && ! -e /proc/net/if_inet6 ]]; then
        return 1
      fi
      return 0
      ;;
    *)
      echo "PROXY_IPV6 must be auto, 1 or 0" >&2
      return 2
      ;;
  esac
}

# Renders the nginx template with the current ports into PROXY_RUNTIME_CONF.
prepare_proxy_conf() {
  normalize_stack_ports || return $?
  local -a flags=()
  local status=0
  proxy_ipv6_enabled || status=$?
  case "$status" in
    0) ;;
    1) flags+=(--no-ipv6) ;;
    *) return "$status" ;;
  esac
  python3 "$ROOT/deploy/dev/render_ports.py" proxy "$PROXY_CONF" "$PROXY_RUNTIME_CONF" \
    "$ENTRY_PORT" "$FRONT_PORT" "$GATEWAY_PORT" ${flags[@]+"${flags[@]}"}
}

# Prints all Docker container names. A failing `docker ps` is reported with
# its own status, never mistaken for "no proxy container".
docker_container_names() {
  local action="$1" names status
  if names="$(docker ps -a --format '{{.Names}}')"; then
    printf '%s\n' "$names"
  else
    status=$?
    echo "failed to list Docker containers while $action $PROXY_NAME" >&2
    return "$status"
  fi
}

# (Re)creates the proxy so it always runs the freshly rendered config.
proxy_up() {
  local was_running=0 running_state container_names
  prepare_proxy_conf || return $?

  # Replace any existing container; remember whether it was already serving so
  # an app-up rollback only removes a proxy that this run brought up.
  container_names="$(docker_container_names starting)" || return $?
  if grep -Fxq -- "$PROXY_NAME" <<<"$container_names"; then
    running_state="$(docker inspect -f '{{.State.Running}}' "$PROXY_NAME")" || return $?
    [[ "$running_state" == "true" ]] && was_running=1
    docker rm -f "$PROXY_NAME" >/dev/null || return $?
  fi

  echo "starting $PROXY_NAME on :$ENTRY_PORT"
  docker run -d --name "$PROXY_NAME" --network host --restart unless-stopped \
    -v "$PROXY_RUNTIME_CONF:/etc/nginx/nginx.conf:ro" \
    nginx:stable-alpine >/dev/null || return $?
  if [[ "$was_running" == "0" ]]; then
    track_app_started_service proxy
  fi

  # nginx exits right away on a bad config; surface that here.
  running_state="$(docker inspect -f '{{.State.Running}}' "$PROXY_NAME")" || return $?
  if [[ "$running_state" != "true" ]]; then
    echo "$PROXY_NAME exited during startup; check its Docker logs" >&2
    return 1
  fi
}

# Removes the proxy container if present.
proxy_down() {
  local container_names
  container_names="$(docker_container_names stopping)" || return $?
  if grep -Fxq -- "$PROXY_NAME" <<<"$container_names"; then
    echo "stopping $PROXY_NAME"
    docker rm -f "$PROXY_NAME" >/dev/null || return $?
  fi
}
