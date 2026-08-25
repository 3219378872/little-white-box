import functools
import os
import subprocess

MYSQL_CONTAINER = os.environ.get("E2E_MYSQL_CONTAINER", "xbh-mysql")
CLICKHOUSE_CONTAINER = os.environ.get("E2E_CLICKHOUSE_CONTAINER", "xbh-clickhouse")
MYSQL_USER = os.environ.get("E2E_MYSQL_USER", "xbh")
MYSQL_PASSWORD = os.environ.get("E2E_MYSQL_PASSWORD", "xbhdev")

_READ_PREFIXES = ("SELECT", "SHOW", "EXPLAIN", "WITH", "DESCRIBE", "DESC")

# Substrings that mean the account itself is missing or rejected (e.g. the
# read-only e2e account was never seeded). Tests skip on DbUnavailable, so
# credential problems must not surface as generic RuntimeError failures.
_DB_AUTH_MARKERS = ("access denied", "error 1045", "error 1410", "error 1698")


class DbUnavailable(RuntimeError):
    pass


@functools.lru_cache(maxsize=None)
def _container_running(name):
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _exec(container, argv, sql):
    if not sql.lstrip().upper().startswith(_READ_PREFIXES):
        raise ValueError(f"read-only SQL required, got: {sql[:60]}")
    if not _container_running(container):
        raise DbUnavailable(f"docker container {container!r} is not running")
    proc = subprocess.run(
        ["docker", "exec", "-i", container, *argv],
        input=sql, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()[:300]
        lowered = stderr.lower()
        if any(marker in lowered for marker in _DB_AUTH_MARKERS):
            raise DbUnavailable(
                f"{container}: database account rejected (seeded by "
                f"middleware-up apply_e2e_db_grants): {stderr[:160]}")
        raise RuntimeError(f"{container}: {stderr}")
    return proc.stdout.strip()


def clickhouse(sql):
    return _exec(CLICKHOUSE_CONTAINER, ["clickhouse-client"], sql)


def mysql(db, sql):
    argv = ["mysql", f"-u{MYSQL_USER}", f"-p{MYSQL_PASSWORD}", "-h127.0.0.1",
            "-N", "-B", db]
    return _exec(MYSQL_CONTAINER, argv, sql)
