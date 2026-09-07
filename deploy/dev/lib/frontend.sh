# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

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
