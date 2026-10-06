#!/usr/bin/env python3
"""Keep local development stdout logs private and bounded."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import gzip
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time


ASSISTANT_LOG_NAMES = ("assistant-rpc", "assistant-agent")


@contextmanager
def log_lock(log_dir: Path):
    """Keep this lock inode in place across both cleanup and rotation."""
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    fd = os.open(log_dir / ".log-maintainer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def clear_logs(log_dir: Path, names=ASSISTANT_LOG_NAMES) -> None:
    """Remove only these services' history, including a dead rotator's temps."""
    with log_lock(log_dir):
        for name in names:
            if name not in ASSISTANT_LOG_NAMES:
                raise ValueError("unsupported sensitive log name")
            log_path = log_dir / f"{name}.log"
            try:
                fd = os.open(log_path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                pass
            else:
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError("sensitive log is not a regular file")
                    os.ftruncate(fd, 0)
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
            log_path.with_suffix(".log.1.gz").unlink(missing_ok=True)
            for temp in log_dir.glob(f"{name}.log.1.gz.*.tmp"):
                temp.unlink()


# Gzips LOG_PATH to <name>.log.1.gz and truncates it once it exceeds MAX_BYTES.
def rotate(log_path: Path, max_bytes: int) -> bool:
    # The entire copy / publish / truncate sequence is one cleanup boundary.
    with log_lock(log_path.parent):
        return _rotate_locked(log_path, max_bytes)


# Rotation body; the caller holds the log lock. Symlinks and small files are
# left alone, and the backup is published atomically via a temp file.
def _rotate_locked(log_path: Path, max_bytes: int) -> bool:
    try:
        info = log_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not log_path.is_file() or log_path.is_symlink():
        return False
    os.chmod(log_path, 0o600)
    if info.st_size <= max_bytes:
        return False

    backup = log_path.with_suffix(log_path.suffix + ".1.gz")
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=backup.name + ".", suffix=".tmp", dir=backup.parent
    )
    try:
        with os.fdopen(temp_fd, "wb") as raw, gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, mtime=0
        ) as compressed, log_path.open("rb") as source:
            shutil.copyfileobj(source, compressed, length=1024 * 1024)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, backup)
        os.chmod(backup, 0o600)
        # Copy-truncate: lines appended between the copy and this truncate
        # are dropped. Services keep their fds open, so this dev-only loss
        # window is accepted over restarting writers on rotation.
        with log_path.open("r+b") as current:
            current.truncate(0)
        return True
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


# One rotation sweep over LOG_DIR/*.log; returns how many logs rotated. A file
# that vanishes or cannot be read is skipped so the loop keeps running.
def maintain(log_dir: Path, max_bytes: int) -> int:
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    rotated = 0
    for path in log_dir.glob("*.log"):
        try:
            rotated += int(rotate(path, max_bytes))
        except OSError:
            continue
    return rotated


# Either clear sensitive assistant logs once, or rotate every --interval.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--clear-assistant", action="store_true")
    parser.add_argument("--clear-name", choices=ASSISTANT_LOG_NAMES)
    args = parser.parse_args()
    if args.max_bytes <= 0 or args.interval <= 0:
        parser.error("max-bytes and interval must be positive")

    if args.clear_assistant or args.clear_name:
        clear_logs(args.log_dir, (args.clear_name,) if args.clear_name else ASSISTANT_LOG_NAMES)
        return 0

    while True:
        maintain(args.log_dir, args.max_bytes)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
