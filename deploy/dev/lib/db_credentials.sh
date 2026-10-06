# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Local MySQL accounts: the app account (runtime DML) and the read-only e2e
# account must be distinct, strong, and the only identities in service DSNs.

# Account names are plain identifiers so they can be embedded in SQL safely.
validate_mysql_account_name() {
  local name="$1" label="$2"
  if [[ ! "$name" =~ ^[A-Za-z0-9_]{1,32}$ ]]; then
    echo "$label must match [A-Za-z0-9_]{1,32}" >&2
    return 1
  fi
  # root is the admin account; xbh was the shared legacy account being retired.
  if [[ "$name" == "root" || "$name" == "xbh" ]]; then
    echo "$label must not use a reserved legacy or root account" >&2
    return 1
  fi
}

# Fails closed unless the env describes two separate, strong accounts and
# every service DSN connects as the app account over tcp.
validate_dev_db_env() {
  local app_user="${APP_MYSQL_USER:-}" app_pass="${APP_MYSQL_PASSWORD:-}"
  local e2e_user="${E2E_MYSQL_USER:-}" e2e_pass="${E2E_MYSQL_PASSWORD:-}"

  # Account names and passwords: valid, distinct, at least 32 characters.
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

  # Service DSNs: none may fall back to another identity.
  ensure_assistant_db_env || return 1
  local key value expected_prefix="${app_user}:${app_pass}@tcp("
  for key in "${DEV_DB_DSN_KEYS[@]}"; do
    value="${!key:-}"
    if [[ -z "$value" || "$value" != "$expected_prefix"* ]]; then
      echo "$key must use APP_MYSQL_USER/APP_MYSQL_PASSWORD over a tcp DSN" >&2
      return 1
    fi
  done
}

# 48 hex chars of randomness; openssl when present, /dev/urandom otherwise.
random_hex_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
    return
  fi
  od -An -N24 -v -tx1 /dev/urandom | tr -d '[:space:]'
}

# Filters env FILE to stdout for a credential rotation: prepends the new
# account lines, drops the old ones, and rewrites each DSN to reference
# ${APP_MYSQL_USER}:${APP_MYSQL_PASSWORD} while keeping host/schema/query.
rewrite_env_with_db_credentials() {
  local file="$1" app_user="$2" app_pass="$3" e2e_user="$4" e2e_pass="$5"
  local line key value quote dsn_tail saw_content=0
  local account_keys="APP_MYSQL_USER|APP_MYSQL_PASSWORD|E2E_MYSQL_USER|E2E_MYSQL_PASSWORD"
  local dsn_keys
  dsn_keys="$(IFS='|'; printf '%s' "${DEV_DB_DSN_KEYS[*]}")"

  printf 'APP_MYSQL_USER=%s\n' "$app_user" || return $?
  printf 'APP_MYSQL_PASSWORD=%s\n' "$app_pass" || return $?
  printf 'E2E_MYSQL_USER=%s\n' "$e2e_user" || return $?
  printf 'E2E_MYSQL_PASSWORD=%s\n' "$e2e_pass" || return $?

  while IFS= read -r line || [[ -n "$line" ]]; do
    # Old account lines are replaced by the header above.
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?($account_keys)[[:space:]]*= ]]; then
      continue
    fi
    # Everything that is not a DSN is copied byte-for-byte.
    if [[ ! "$line" =~ ^[[:space:]]*(export[[:space:]]+)?($dsn_keys)[[:space:]]*= ]]; then
      printf '%s\n' "$line" || return $?
      continue
    fi

    # DSN: strip optional matching quotes, then keep everything from @tcp( on.
    key="${BASH_REMATCH[2]}"
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    quote=""
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
      quote="${value:0:1}"
      if [[ "${value: -1}" != "$quote" ]]; then
        echo "$key has an unterminated quoted DSN" >&2
        return 1
      fi
      value="${value:1:${#value}-2}"
    fi
    if [[ "$value" != *"@tcp("* || "$value" != *")/"* ]]; then
      echo "$key is not a supported tcp MySQL DSN" >&2
      return 1
    fi
    dsn_tail="tcp(${value##*@tcp(}"
    # shellcheck disable=SC2016 # the ${...} references are written literally
    printf '%s="${APP_MYSQL_USER}:${APP_MYSQL_PASSWORD}@%s"\n' "$key" "$dsn_tail" || return $?
    [[ "$key" != "DB_CONTENT" ]] || saw_content=1
  done <"$file" || return $?

  # DB_CONTENT is the template other schemas are derived from.
  if [[ "$saw_content" != "1" ]]; then
    echo "DB_CONTENT is required before rotating MySQL credentials" >&2
    return 1
  fi
}

# Atomically rotate only the local app/e2e MySQL credentials. Provider keys,
# gateways and all non-DB settings are copied byte-for-byte. DB DSN hosts,
# schemas and query strings are preserved while credentials become references
# to the new app variables. Values are never printed.
rotate_dev_db_credentials_locked() {
  local file app_user="xbh_app" e2e_user="xbh_e2e" app_pass e2e_pass tmp status
  file="$(resolve_env_file)" || return $?
  if [[ -L "$file" || ! -f "$file" ]]; then
    echo "dev env must be a regular non-symlink file" >&2
    return 1
  fi
  chmod 600 "$file" || return $?

  # Two fresh, independent secrets.
  app_pass="$(random_hex_secret)" || return 1
  e2e_pass="$(random_hex_secret)" || return 1
  if [[ ! "$app_pass" =~ ^[0-9a-f]{48}$ || ! "$e2e_pass" =~ ^[0-9a-f]{48}$ || "$app_pass" == "$e2e_pass" ]]; then
    echo "failed to generate independent MySQL credentials" >&2
    return 1
  fi

  # Write the rewritten env next to the original (same filesystem), then
  # rename over it so a failure never leaves a half-rotated file.
  tmp="$(mktemp "$(dirname "$file")/.xbh-dev-env.XXXXXX")" || return $?
  if chmod 600 "$tmp"; then
    :
  else
    status=$?
    rm -f "$tmp"
    return "$status"
  fi
  if rewrite_env_with_db_credentials "$file" "$app_user" "$app_pass" "$e2e_user" "$e2e_pass" >"$tmp"; then
    :
  else
    status=$?
    rm -f "$tmp" 2>/dev/null || true
    return "$status"
  fi
  mv -f "$tmp" "$file" || return $?
  chmod 600 "$file" || return $?
  echo "rotated local app/e2e MySQL credentials in $file"
  # MySQL still holds the old passwords until middleware-up reseeds them; an
  # app-up before that would start every service with rejected credentials.
  echo "next: run 'just up' (app-down, middleware-up, app-up) to apply them"
}

# Public entry: rotation rewrites the env file, so it takes the lifecycle lock.
rotate_dev_db_credentials() {
  with_app_lifecycle_lock exclusive rotate_dev_db_credentials_locked
}
