import importlib.util
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import time


ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy" / "dev" / "stack.sh"
JUSTFILE = ROOT / "justfile"


def load_script_module(name, path):
    """Import a standalone helper script (not a package module) for unit tests."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_env(run_dir, *, etc_dir=None, lock=None):
    """RUN_DIR with its logs/ and pids/ (plus optional ETC_DIR and lock) for a test."""
    env = {"RUN_DIR": run_dir, "LOG_DIR": run_dir / "logs", "PID_DIR": run_dir / "pids"}
    if etc_dir is not None:
        env["ETC_DIR"] = etc_dir
    if lock is not None:
        env["APP_LIFECYCLE_LOCK"] = lock
    return env


def source_stack(**env):
    """Bash preamble: export ROOT and ENV (in order), then source stack.sh."""
    lines = [f"export ROOT={shlex.quote(str(ROOT))}"]
    lines += [f"export {key}={shlex.quote(str(value))}" for key, value in env.items()]
    lines.append(f"source {shlex.quote(str(STACK))}")
    return "\n".join(lines)


def run_bash(script, *, check=True, timeout=30):
    """Run SCRIPT under `bash -euo pipefail` from the repo root."""
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"bash failed with {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def stop_test_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def unused_loopback_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_port(port, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"process did not listen on 127.0.0.1:{port}")


def wait_for_path(path, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"process did not create {path}")


def process_is_running(pid):
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return False
    state = stat_line.rsplit(") ", 1)[1].split(" ", 1)[0]
    return state not in {"X", "Z"}


def stop_test_pid_group(pid):
    for sig, timeout in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        if not process_is_running(pid):
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while process_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
    if process_is_running(pid):
        raise AssertionError(f"test process group {pid} did not stop")


def stack_source(stack=STACK):
    """Read only modules explicitly sourced by the public entrypoint."""
    shim = stack.read_text(encoding="utf-8")
    names = re.findall(r'^source "\$\{_XBH_STACK_LIB_DIR\}/([a-z_]+\.sh)"', shim, re.MULTILINE)
    if not names or len(names) != len(set(names)):
        raise AssertionError("stack.sh must load an explicit, unique module list")
    return "\n".join([shim, *((stack.parent / "lib" / name).read_text(encoding="utf-8") for name in names)])
