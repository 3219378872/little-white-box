import time


class EventuallyFailed(AssertionError):
    pass


def eventually(check, desc="condition", timeout=90.0, interval=2.0):
    deadline = time.monotonic() + timeout
    last = None
    while True:
        last = check()
        if last:
            return last
        if time.monotonic() >= deadline:
            raise EventuallyFailed(f"{desc} not satisfied within {timeout:.0f}s (last={last!r})")
        time.sleep(interval)
