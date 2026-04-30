from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from bai.core.io import fsync_directory


def test_fsync_directory_surfaces_open_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(path: Path, flags: int) -> int:
        raise FileNotFoundError("missing directory")

    monkeypatch.setattr(os, "open", fail_open)

    with pytest.raises(FileNotFoundError, match="missing directory"):
        fsync_directory(Path("missing"))


def test_fsync_directory_tolerates_unsupported_directory_fsync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    monkeypatch.setattr(os, "open", lambda path, flags: 42)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(
            OSError(errno.EINVAL, "directory fsync unsupported")
        ),
    )
    monkeypatch.setattr(os, "close", lambda descriptor: closed.append(descriptor))

    fsync_directory(Path("runtime-dir"))

    assert closed == [42]


def test_fsync_directory_surfaces_permission_failures_after_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    monkeypatch.setattr(os, "open", lambda path, flags: 42)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr(os, "close", lambda descriptor: closed.append(descriptor))

    with pytest.raises(PermissionError, match="denied"):
        fsync_directory(Path("runtime-dir"))

    assert closed == [42]
