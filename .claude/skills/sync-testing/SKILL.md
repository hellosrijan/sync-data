---
name: sync-testing
description: Testing techniques specific to the sync engine - an injected clock, transactional concurrency tests, restart-while-down, measuring the request rate, and the brief's four required checks. Use when Ross writes tests for apply(), the outbox worker, the limiter, or the demo script (build-plan 2.4, 5.5, 8.1-8.2).
---

# Testing the sync engine

These are techniques, not what to test — the acceptance criteria in each task's ledger say
what. This skill is for the parts that don't fit normal Django test patterns: time, threads,
and a real second process.

## 1. Injected clock — never freezegun

`agent-code-review`'s own rules already say: control time by passing timestamps explicitly or
patching `django.utils.timezone.now`, and don't add freezegun. In this codebase that means:

- **Business logic takes `occurred_at`/`now` as a value**, not by calling `timezone.now()`
  deep inside — `apply()` already does this via `Change.occurred_at`. Where code *must* call
  `timezone.now()` itself (the outbound task picking due rows, the probe's interval), patch it:

  ```python
  from unittest.mock import patch
  with patch("django.utils.timezone.now", return_value=fixed_time):
      ...
  ```

- For the rate-limiter test (5.2, "100 calls take ≥ 19 s"), don't actually sleep in the test.
  Give the limiter an injectable clock function (`time.monotonic` by default, overridable) and
  advance a fake clock between calls instead of wall-clock sleeping. If the limiter's Redis Lua
  script needs real elapsed time, run it against real Redis but keep the test's *assertions*
  tolerant (`>= 19`, not `== 19.0`) — Redis's own clock isn't mockable from Python, so this one
  spot is allowed to use a short real sleep loop rather than a frozen clock. Say so in a
  comment if you do.

## 2. Transactional concurrency tests (2.4)

Django's test runner wraps each test in a transaction and rolls it back — that's wrong for a
test that needs **two genuinely separate transactions to interleave** (two threads both
hitting `SELECT ... FOR UPDATE` on the same row). Use
`django.test.TransactionTestCase` (not `TestCase`) for these, and real threads:

```python
class ConcurrentApplyTests(TransactionTestCase):
    def test_two_threads_same_record(self):
        customer = CustomerFactory()
        barrier = threading.Barrier(2)
        results = []

        def worker(change):
            barrier.wait()                      # make both threads reach the lock at once
            results.append(apply(change))

        t1 = threading.Thread(target=worker, args=(change_a,))
        t2 = threading.Thread(target=worker, args=(change_b,))
        t1.start(); t2.start(); t1.join(); t2.join()

        customer.refresh_from_db()
        # assert the final state is exactly what "newest occurred_at per field wins" predicts,
        # and that BOTH FieldChange rows exist (one applied, one superseded or both applied
        # if different fields) — not just that it "didn't crash".
```

Each thread needs its **own DB connection** (Django gives threads separate connections
automatically under `TransactionTestCase`) — don't share a connection across threads.

Run this kind of test **10 times in a row** in CI/locally before trusting it (build-plan 2.4's
own "done when"): `pytest --count=10 path::test -q` if `pytest-repeat` is available, otherwise
a plain `for i in range(10): ...` loop inside the test. A lock-based test that passes once can
still have a race; running it once is not evidence.

## 3. Restart-while-down (5.5, a `brief`-marked check)

This is the one test that needs a **second OS process**, because "the worker restarts" has to
mean a real process dying and a new one starting, not just calling a function twice in the
same test process.

```python
@pytest.mark.brief
def test_restart_while_down(local_fake_server):   # fixture from sync-test-scenarios, 4.1.3
    server, port = local_fake_server
    fake.mode = "down"
    # ... make portal edits via apply(), so Outbox rows exist ...

    proc = subprocess.Popen(
        ["uv", "run", "python", "src/manage.py", "rundramatiq", "--queues", "default"],
        env={**os.environ, "SALESFORCE_BASE_URL": f"http://127.0.0.1:{port}"},
    )
    time.sleep(2)                 # let it pick up the (failing) due rows at least once
    proc.kill(); proc.wait()      # simulate the worker dying mid-outage

    fake.mode = "up"
    proc2 = subprocess.Popen([...])   # a fresh worker process
    try:
        wait_until(lambda: all(r.salesforce_id for r in Contact.objects.all()), timeout=30)
    finally:
        proc2.kill(); proc2.wait()

    assert fake.calls  # the edits actually reached FakeSalesforce, not just "no crash"
```

- Use a short polling `wait_until` helper (no `time.sleep(30)` — poll every 0.2s up to a
  timeout and fail with a clear message if it never becomes true).
- Always `kill()` and `wait()` subprocesses in a `finally`, even on assertion failure, or a
  stray worker process will keep running after the test session ends.
- This test is slow (real process startup) relative to the rest of the suite — keep it as the
  only test that does this; everything else in phase 5 uses `MockTransport`.

## 4. Measuring the request rate (8.1.3)

Don't assert on wall-clock timing for the *correctness* of the limiter (flaky). Assert on
`FakeSalesforce.calls` timestamps instead: group calls into 1-second buckets and check no
bucket exceeds the configured limit. This is also what `scripts/demo.sh` prints — reuse the
same bucketing function for both the test and the demo script, don't write it twice.

## 5. The brief's four checks, end to end (`pytest -m brief`)

These are the only tests `scripts/demo.sh` runs (8.1.2), each already built by its own phase:

| # | Check | Built in | Test shape |
|---|---|---|---|
| 1 | Duplicate webhook → one change | phase 3 | post `scenarios.sales_edit()` twice, assert one `FieldChange` |
| 2 | Older-after-newer → newest kept, both in history | phase 2/3 | post newer, then `scenarios.stale_after(newer, ...)`, assert current value + 2 `FieldChange` rows |
| 3 | No echo back to Salesforce | phase 3 | post a webhook, assert `Outbox.objects.count() == 0` and `fake.calls == []` |
| 4 | Restart while down | phase 5 | section 3 above |

Mark exactly these four `@pytest.mark.brief` as each is built — don't mark anything else, so
`pytest -m brief` stays a fast, exact match for the demo.

## Pre-existing team rule this skill doesn't repeat

`.claude/rules/drf.md`'s "Before handing off" commands (`check`, `makemigrations --check`,
`pytest -q`) still apply to every phase in `sync-engine`. This skill only adds what's specific
to concurrency, time and process-level testing.
