"""Trusted direct test-command subprocess adapter with bounded output capture.

The adapter runs an explicitly allowed argv with shell=False in the workspace
root. It bounds retained stdout/stderr but does not sandbox filesystem writes
or manage process trees beyond killing the immediate child on timeout.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..core.errors import BaiUserError

TEST_OUTPUT_PREVIEW_CHARS = 4096
TEST_COMMAND_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class TestCommandExecutor:
    timeout_seconds: float = TEST_COMMAND_TIMEOUT_SECONDS
    preview_chars: int = TEST_OUTPUT_PREVIEW_CHARS

    def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        declared_writable_paths: list[str],
    ) -> dict[str, object]:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        stdout_thread = threading.Thread(
            target=self._drain_stream,
            args=(process.stdout, stdout_buffer),
        )
        stderr_thread = threading.Thread(
            target=self._drain_stream,
            args=(process.stderr, stderr_buffer),
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            exit_code = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            stdout_thread.join()
            stderr_thread.join()
            raise BaiUserError("test command timed out") from exc
        stdout_thread.join()
        stderr_thread.join()
        return {
            "argv": argv,
            "cwd": str(cwd),
            "exit_code": exit_code,
            "stdout_preview": self._preview(stdout_buffer),
            "stderr_preview": self._preview(stderr_buffer),
            "stdout_truncated": len(stdout_buffer) > self.preview_chars,
            "stderr_truncated": len(stderr_buffer) > self.preview_chars,
            "declared_writable_paths": declared_writable_paths,
            "write_enforcement": "trusted_command_no_sandbox",
        }

    def _drain_stream(self, stream: BinaryIO | None, buffer: bytearray) -> None:
        if stream is None:
            return
        limit = self.preview_chars + 1
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = limit - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])

    def _preview(self, buffer: bytearray) -> str:
        return bytes(buffer[: self.preview_chars]).decode("utf-8", errors="replace")
