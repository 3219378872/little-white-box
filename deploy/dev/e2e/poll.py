import time


# Raised when a polled condition never held; an AssertionError for pytest.
class EventuallyFailed(AssertionError):
    pass


# Retries CHECK every INTERVAL seconds until it returns a truthy value, which
# is returned; eventual consistency (MQ, indexes, caches) needs this in e2e.
def eventually(check, desc="condition", timeout=90.0, interval=2.0):
    # A transient 503 or half-written body raising inside check is retried
    # like a falsy result; the last error is chained once the deadline passes.
    # pytest outcomes (skip/fail) derive from BaseException and still escape.
    deadline = time.monotonic() + timeout
    last = None
    last_error = None
    while True:
        try:
            last = check()
        except Exception as exc:
            last, last_error = None, exc
        else:
            if last:
                return last
            last_error = None
        if time.monotonic() >= deadline:
            detail = f"last error={last_error!r}" if last_error else f"last={last!r}"
            raise EventuallyFailed(
                f"{desc} not satisfied within {timeout:.0f}s ({detail})") from last_error
        time.sleep(interval)
