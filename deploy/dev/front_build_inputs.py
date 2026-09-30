#!/usr/bin/env python3
"""Content-address release inputs without invoking or bootstrapping Flutter."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def fingerprint(frontend: Path, flutter: Path, flags: list[str]) -> str:
    digest = hashlib.sha256()

    def record(value):
        digest.update(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode())
        digest.update(b'\n')

    def visit(path: Path, label: str, parents=frozenset()):
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode):
            record([label, 'link', os.readlink(path)])
        resolved = path.resolve(strict=True)
        if resolved in parents:
            raise ValueError(f'cyclic build input: {label}')
        if path.is_dir():
            record([label, 'directory'])
            for child in sorted(path.iterdir()):
                visit(child, f'{label}/{child.name}', parents | {resolved})
        elif path.is_file():
            file_digest = hashlib.sha256()
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    file_digest.update(chunk)
                finished = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns
            ):
                raise ValueError(f'build input changed while hashing: {label}')
            record([label, 'file', file_digest.hexdigest()])
        else:
            raise ValueError(f'unsupported build input: {label}')
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError(f'build input changed while scanning: {label}')

    record(['schema', 1, 'flags', flags])
    for name in ('lib', 'web', 'pubspec.yaml', 'pubspec.lock'):
        visit(frontend / name, name)
    for name in ('assets', 'packages', 'vendor'):
        path = frontend / name
        if path.exists() or path.is_symlink():
            visit(path, name)
        else:
            record([name, 'absent'])
    flutter = flutter.resolve(strict=True)
    record(['flutter', str(flutter)])
    visit(flutter, 'flutter-executable')
    # This populated SDK identity is required: do not run flutter --version,
    # which can bootstrap/download artifacts during a cache check.
    visit(flutter.parent / 'cache/flutter.version.json', 'flutter-sdk-version')
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('frontend', type=Path)
    parser.add_argument('flutter', type=Path)
    parser.add_argument('flags', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        print(fingerprint(args.frontend, args.flutter, args.flags))
    except (OSError, ValueError) as error:
        print(f'cannot fingerprint frontend build inputs: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
