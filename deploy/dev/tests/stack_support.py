import os
from pathlib import Path
import re
import signal
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "deploy" / "dev" / "stack.sh"
JUSTFILE = ROOT / "justfile"


def run_bash(script, *, check=True, timeout=30):
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
