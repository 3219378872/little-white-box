#!/usr/bin/env python3
"""Keep local development stdout logs private and bounded."""

from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path
import shutil
import tempfile
import time


def rotate(log_path: Path, max_bytes: int) -> bool:
    try:
        stat = log_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not log_path.is_file() or log_path.is_symlink():
        return False
    os.chmod(log_path, 0o600)
    if stat.st_size <= max_bytes:
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
        with log_path.open("r+b") as current:
            current.truncate(0)
        return True
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.max_bytes <= 0 or args.interval <= 0:
        parser.error("max-bytes and interval must be positive")

    while True:
        maintain(args.log_dir, args.max_bytes)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
