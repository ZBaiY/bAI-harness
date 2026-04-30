"""Small filesystem helpers for atomic JSON/text artifact writes.

Artifact stores use these helpers to avoid partial JSON files where practical.
They deliberately stay small and local: no lock manager, transaction log, or
cross-process persistence framework is introduced here.
"""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path

UNSUPPORTED_DIRECTORY_FSYNC_ERRNOS = {
    code
    for code in (
        errno.EINVAL,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOSYS", None),
    )
    if code is not None
}


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno in UNSUPPORTED_DIRECTORY_FSYNC_ERRNOS:
                return
            raise
    finally:
        os.close(descriptor)


def write_temp_text(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{name}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    temp_path = write_temp_text(path.parent, path.name, content)
    try:
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
