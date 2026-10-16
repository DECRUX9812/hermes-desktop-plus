"""Hermes CLI subprocess boundary."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

from hermes_plus.adapter.paths import hermes_home
from hermes_plus.adapter.version import resolve_hermes_bin


def run_hermes(
    args: list[str],
    *,
    profile: str | None = None,
    timeout: int = 300,
    relay_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the installed Hermes CLI, binding an optional profile through HERMES_HOME.

    The generous default covers a cold HERMES_HOME: the stock CLI lazily
    prepares its isolated runtime (60-110s+) on first real command there.
    ``relay_stderr`` copies the CLI's stderr to ours while it runs, so a
    first-time dependency sync shows its progress instead of looking like a hang.
    """
    binary = resolve_hermes_bin()
    if binary is None:
        raise FileNotFoundError(
            "Hermes executable not found; set HERMES_PLUS_HERMES_BIN or add hermes to PATH"
        )
    env = os.environ.copy()
    if profile is not None:
        env["HERMES_HOME"] = str(hermes_home(profile))
    command = [binary, *args]
    if relay_stderr:
        return _run_relaying_stderr(command, env, timeout)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _run_relaying_stderr(
    command: list[str], env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run *command*, echoing its stderr as it arrives, and keep both streams.

    The caller still gets the captured output, and a timeout kills the child and
    raises the same ``TimeoutExpired`` a ``subprocess.run`` timeout would. The child
    gets a session of its own so that a timeout can take down the worker process the
    stock CLI hands its dependency sync to; killing the CLI alone would leave that
    worker building an environment for minutes, still writing to our stderr.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    captured: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def drain(name: str, echo: bool) -> None:
        stream = getattr(process, name)
        assert stream is not None
        for line in stream:
            captured[name].append(line)
            if echo:
                sys.stderr.write(line)
                sys.stderr.flush()

    readers = [
        threading.Thread(target=drain, args=("stdout", False), daemon=True),
        threading.Thread(target=drain, args=("stderr", True), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        for reader in readers:
            # A grandchild that outlived the kill must not turn a timeout into a hang.
            reader.join(timeout=5)
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output="".join(captured["stdout"]),
            stderr="".join(captured["stderr"]),
        ) from None
    except BaseException:
        # KeyboardInterrupt and friends: do not leave the sync running behind us.
        _kill_process_tree(process)
        raise
    for reader in readers:
        reader.join()
    return subprocess.CompletedProcess(
        command, returncode, "".join(captured["stdout"]), "".join(captured["stderr"])
    )


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Kill the child and everything it spawned, then reap the child."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, AttributeError):  # already gone, not a group leader, or no killpg (Windows)
        process.kill()
    process.wait()

