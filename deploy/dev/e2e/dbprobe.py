import functools
import os
import subprocess

MYSQL_CONTAINER = os.environ.get("E2E_MYSQL_CONTAINER", "xbh-mysql")
CLICKHOUSE_CONTAINER = os.environ.get("E2E_CLICKHOUSE_CONTAINER", "xbh-clickhouse")
MYSQL_USER = os.environ.get("E2E_MYSQL_USER", "")
MYSQL_PASSWORD = os.environ.get("E2E_MYSQL_PASSWORD", "")

# No "WITH": MySQL 8 accepts "WITH ... DELETE/UPDATE", and no probe needs a CTE.
_READ_PREFIXES = ("SELECT", "SHOW", "EXPLAIN", "DESCRIBE", "DESC")

# Substrings that mean the account itself is missing or rejected (e.g. the
# read-only e2e account was never seeded). Tests skip on DbUnavailable, so
# credential problems must not surface as generic RuntimeError failures.
_DB_AUTH_MARKERS = ("access denied", "error 1045", "error 1410", "error 1698",
                    "authentication_failed")


# The database cannot be probed in this environment; tests skip on it.
class DbUnavailable(RuntimeError):
    pass


# Cached per run: a stopped container stays stopped for the whole session.
@functools.lru_cache(maxsize=None)
def _container_running(name):
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _require_single_read(sql):
    # A prefix check alone accepts "SELECT 1; DROP ..." because both CLIs read
    # several statements from stdin. Grants remain the real boundary; this
    # guard keeps probes to one statement. Probes never need a literal ";".
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise ValueError(f"single read-only statement required, got: {sql[:60]}")
    if not statement.upper().startswith(_READ_PREFIXES):
        raise ValueError(f"read-only SQL required, got: {sql[:60]}")
    return statement


# Runs one read-only statement inside CONTAINER, passing secrets only via
# environment variables (never argv, which `ps` would show).
def _exec(container, argv, sql, *, env=None):
    sql = _require_single_read(sql)
    if not _container_running(container):
        raise DbUnavailable(f"docker container {container!r} is not running")
    child_env = os.environ.copy()
    command = ["docker", "exec", "-i"]
    for name, value in sorted((env or {}).items()):
        child_env[name] = value
        command.extend(["-e", name])
    proc = subprocess.run(
        [*command, container, *argv], env=child_env,
        input=sql, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()[:300]
        lowered = stderr.lower()
        if any(marker in lowered for marker in _DB_AUTH_MARKERS):
            raise DbUnavailable(
                f"{container}: database account rejected (seeded by "
                f"middleware-up apply_dev_db_grants / "
                f"apply_clickhouse_e2e_grants): {stderr[:160]}")
        raise RuntimeError(f"{container}: {stderr}")
    return proc.stdout.strip()


# The read-only e2e account comes from the root env file via `just e2e`.
def _require_e2e_credentials():
    if not MYSQL_USER or not MYSQL_PASSWORD:
        raise DbUnavailable(
            "E2E_MYSQL_USER/E2E_MYSQL_PASSWORD were not loaded; run the "
            "root e2e recipe with a rotated dev env")


def clickhouse(sql):
    # Same read-only e2e identity as MySQL (seeded by middleware-up
    # apply_clickhouse_e2e_grants), never the unrestricted `default` user.
    # clickhouse-client reads both values from the environment.
    _require_e2e_credentials()
    return _exec(CLICKHOUSE_CONTAINER, ["clickhouse-client"], sql,
                 env={"CLICKHOUSE_USER": MYSQL_USER,
                      "CLICKHOUSE_PASSWORD": MYSQL_PASSWORD})


# Read-only MySQL probe as the e2e account against database DB.
def mysql(db, sql):
    _require_e2e_credentials()
    argv = ["mysql", f"-u{MYSQL_USER}", "-h127.0.0.1", "-N", "-B", db]
    return _exec(MYSQL_CONTAINER, argv, sql, env={"MYSQL_PWD": MYSQL_PASSWORD})
