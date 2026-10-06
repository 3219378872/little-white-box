# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Flutter web frontend: a release bundle built from FRONTEND and served by
# serve_release.py. The bundle is rebuilt only when its input fingerprint
# changes (or FORCE_FRONT_BUILD=1).

# Prints the root of the Flutter SDK on PATH; fails when flutter is missing.
flutter_sdk_dir() {
  local flutter
  flutter="$(command -v flutter 2>/dev/null || true)"
  [[ -n "$flutter" ]] || return 1
  (cd "$(dirname "$(readlink -f "$flutter")")/.." 2>/dev/null && pwd)
}

# Local Flutter web engine assets (CanvasKit/Skwasm). Since Flutter 3.44 the
# engine reads its base URL from a compile-time dart-define only, so the dev
# server must serve the assets itself: symlink the SDK cache into the app's
# web/ dir and pass --dart-define=FLUTTER_WEB_CANVASKIT_URL=/canvaskit/.
# The link re-points on every app-up, following SDK upgrades automatically.
ensure_web_canvaskit() {
  local sdk src dst
  sdk="$(flutter_sdk_dir)" || return 1
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

# Prints the CanvasKit base URL baked into the release build. Prefer
# same-origin assets (web/canvaskit -> SDK cache); fall back to the
# engine-revision-pinned gstatic URL when the SDK cache is unavailable, and to
# /canvaskit/ when even the engine revision is unknown.
resolve_canvaskit_url() {
  local sdk stamp rev
  if ensure_web_canvaskit; then
    printf '%s\n' /canvaskit/
    return 0
  fi
  echo "warning: flutter_web_sdk canvaskit dir not found, using gstatic fallback" >&2
  sdk="$(flutter_sdk_dir || true)"
  stamp="$sdk/bin/cache/engine_stamp.json"
  if [[ -f "$stamp" ]]; then
    rev="$(sed -n 's/.*"git_revision": *"\([^"]*\)".*/\1/p' "$stamp" | head -n1)"
    if [[ -n "$rev" ]]; then
      printf 'https://www.gstatic.com/flutter-canvaskit/%s/\n' "$rev"
      return 0
    fi
  fi
  printf '%s\n' /canvaskit/
}

# Fingerprint paths + contents + build flags + populated SDK identity. No Flutter
# command is executed by this check; unreadable/missing inputs fail closed.
front_build_fingerprint() {
  local flutter
  flutter="$(command -v flutter)" || return $?
  python3 "$ROOT/deploy/dev/front_build_inputs.py" "$FRONTEND" "$flutter" "$@"
}

# True when the stamp from the last successful build matches current inputs.
front_bundle_fresh() {
  local current
  [[ -f "$FRONT_BUILD_STAMP" ]] || return 1
  current="$(front_build_fingerprint "$@")" || return $?
  [[ "$current" == "$(<"$FRONT_BUILD_STAMP")" ]]
}

# Rebuilds BUNDLE with `flutter BUILD_ARGS...` unless it is already fresh.
# The stamp is removed before building and written only after a build whose
# inputs did not change underneath it, so a stale stamp can never vouch for a
# half-written or outdated bundle.
build_front_bundle_if_stale() {
  local bundle="$1" logfile="$2"
  shift 2
  local before after stamp_tmp build_status

  # Decide whether a build is needed at all.
  if [[ "${FORCE_FRONT_BUILD:-0}" != "1" && -f "$bundle/index.html" ]] &&
    front_bundle_fresh "$@"; then
    echo "frontend bundle up to date; set FORCE_FRONT_BUILD=1 to rebuild"
    return 0
  fi

  # Build with proxies unset: pub/engine downloads must not go through them.
  before="$(front_build_fingerprint "$@")" || return $?
  rm -f "$FRONT_BUILD_STAMP" || return $?
  echo "building frontend (release)..."
  if (
    cd "$FRONTEND" || exit $?
    env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
      -u ALL_PROXY -u all_proxy \
      flutter "$@"
  ) >>"$logfile" 2>&1; then
    :
  else
    build_status=$?
    rm -f "$FRONT_BUILD_STAMP"
    echo "frontend build failed; refusing to serve an existing bundle; see $logfile" >&2
    return "$build_status"
  fi

  # Reject a bundle whose sources moved during the build.
  after="$(front_build_fingerprint "$@")" || return $?
  if [[ "$before" != "$after" ]]; then
    echo "frontend inputs changed during build; refusing to serve; retry app-up" >&2
    return 1
  fi
  [[ -f "$bundle/index.html" ]] || {
    echo "frontend build did not produce index.html" >&2
    return 1
  }

  # Publish the stamp atomically.
  stamp_tmp="$(mktemp "$FRONT_BUILD_STAMP.XXXXXX")" || return $?
  if ! printf '%s\n' "$after" >"$stamp_tmp" ||
    ! mv -f "$stamp_tmp" "$FRONT_BUILD_STAMP"; then
    rm -f "$stamp_tmp"
    return 1
  fi
}

# Builds (if needed) and serves the frontend release bundle on FRONT_PORT.
frontend_up() {
  normalize_stack_ports || return $?
  local pidfile="$PID_DIR/frontend.pid"
  local logfile="$LOG_DIR/frontend.log"
  local bundle="$FRONTEND/build/web"
  local pid canvaskit_url
  secure_runtime_paths || return $?
  touch "$logfile" || return $?
  chmod 600 "$logfile" || return $?

  # Keep a running server unless a rebuild was forced.
  if pid="$(validated_service_pid frontend "$pidfile")"; then
    if [[ "${FORCE_FRONT_BUILD:-0}" != "1" ]]; then
      echo "already running: frontend pid=$pid"
      return 0
    fi
    # Stop before modifying the served directory. A failed forced rebuild leaves
    # the frontend stopped rather than serving an old or partially written bundle.
    stop_svc frontend || return $?
  fi
  prepare_service_start_state frontend "$pidfile" || return $?

  canvaskit_url="$(resolve_canvaskit_url)" || return $?
  local -a build_args=(build web --release -t lib/main.dart --no-web-resources-cdn
    "--dart-define=FLUTTER_WEB_CANVASKIT_URL=$canvaskit_url")
  build_front_bundle_if_stale "$bundle" "$logfile" "${build_args[@]}" || return $?

  echo "starting frontend on :$FRONT_PORT (static release bundle)"
  launch_managed_process frontend "$pidfile" "$logfile" "$FRONTEND" -- \
    python3 "$FRONT_SERVER_SCRIPT" "$FRONT_PORT" "$bundle" || return $?
  track_app_started_service frontend
}
