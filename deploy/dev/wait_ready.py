#!/usr/bin/env python3
"""Bound all readiness probes and sleeps by one monotonic deadline."""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time


# Polls PROBE(remaining) until it succeeds or SECONDS pass. A success that
# lands after the deadline does not count, and sleeps never overshoot it.
def wait(probe, seconds: float, *, clock=time.monotonic, sleep=time.sleep) -> bool:
    deadline = clock() + seconds
    while (remaining := deadline - clock()) > 0:
        if probe(remaining) and clock() <= deadline:
            return True
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(1.0, remaining))
    return False


# Runs one probe command bounded by the remaining time; http probes must print
# status 200, others may require exact stdout EXPECT.
def run_probe(command: list[str], remaining: float, http=False, expect: bytes | None = None) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, timeout=remaining, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if http:
        expect = b'200'
    return result.returncode == 0 and (expect is None or result.stdout.strip() == expect)


# CLI: wait_ready.py {http URL|port HOST PORT|healthy CONTAINER} SECONDS LABEL
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('http', 'port', 'healthy'))
    parser.add_argument('seconds', type=float)
    parser.add_argument('label')
    parser.add_argument('target', nargs='+')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error('readiness seconds must be a finite nonnegative number')
    if args.kind == 'http':
        if len(args.target) != 1:
            parser.error('http expects one URL')
        def probe(remaining):
            return run_probe(['curl', '-sS', '-o', '/dev/null', '-w', '%{http_code}',
                              '--max-time', str(min(3.0, remaining)), args.target[0]], remaining, http=True)
    elif args.kind == 'healthy':
        if len(args.target) != 1:
            parser.error('healthy expects one container name')
        # A published port opens as soon as the container starts, long before the
        # server inside accepts work; the container healthcheck is the real signal.
        def probe(remaining):
            return run_probe(['docker', 'inspect', '-f', '{{.State.Health.Status}}', args.target[0]],
                             remaining, expect=b'healthy')
    else:
        if len(args.target) != 2:
            parser.error('port expects host and port')
        def probe(remaining):
            # The subprocess deadline also bounds DNS, which socket timeouts do not.
            return run_probe([sys.executable, '-c',
                              'import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=float(sys.argv[3])).close()',
                              *args.target, str(min(1.0, remaining))], remaining)
    if wait(probe, args.seconds):
        print(f'ready: {args.label}')
        return 0
    print(f'timeout waiting for {args.label}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
