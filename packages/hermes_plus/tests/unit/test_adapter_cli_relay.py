"""`run_hermes(relay_stderr=True)` copies the CLI's stderr to ours while it runs.

Real subprocesses, no mocks: the child is a plain Python process writing to both
streams, the way the stock CLI writes its provisioning progress to stderr.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from hermes_plus.adapter.cli import _run_relaying_stderr

ECHO_BOTH = "import sys; print('out-line'); print('err-line', file=sys.stderr, flush=True)"
SLEEP_AFTER_LINE = (
    "import sys, time; print('first-line', file=sys.stderr, flush=True); time.sleep(60)"
)
# The stock CLI hands its dependency sync to a worker process; the timeout has to
# reach that worker, not just the CLI.
SPAWN_WORKER = textwrap.dedent(
    """
    import subprocess, sys, time
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(worker.pid, flush=True)
    time.sleep(60)
    """
)


def test_stderr_is_relayed_while_stdout_is_only_captured(capsys) -> None:
    result = _run_relaying_stderr([sys.executable, "-c", ECHO_BOTH], os.environ.copy(), 30)
    assert result.returncode == 0
    assert result.stdout == "out-line\n"
    assert result.stderr == "err-line\n"
    relayed = capsys.readouterr()
    assert "err-line" in relayed.err
    assert "out-line" not in relayed.err


def test_a_timeout_kills_the_child_and_keeps_what_it_said(capsys) -> None:
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _run_relaying_stderr([sys.executable, "-c", SLEEP_AFTER_LINE], os.environ.copy(), 2)
    assert failure.value.cmd[0] == sys.executable
    assert failure.value.timeout == 2
    assert "first-line" in (failure.value.stderr or "")
    assert "first-line" in capsys.readouterr().err


def test_a_timeout_takes_down_the_worker_the_child_started() -> None:
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _run_relaying_stderr([sys.executable, "-c", SPAWN_WORKER], os.environ.copy(), 3)
    worker = int((failure.value.output or "").strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(worker, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"the worker the child started (pid {worker}) is still running")
