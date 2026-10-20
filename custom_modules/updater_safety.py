"""Durable local state and process locks shared by updater entrypoints."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import shutil
import tempfile

import psutil


class FatalUpdaterError(RuntimeError):
    """An operator must resolve a local safety/configuration failure."""


class RetryableUpdaterError(RuntimeError):
    """A bounded attempt failed; retry without discarding owned work."""


class SourceDataError(RetryableUpdaterError):
    """The remote snapshot is not safe to publish."""


class LockUnavailable(FatalUpdaterError):
    pass


def kill_process_tree(pid: int) -> None:
    """Kill and reap a supervised process and its descendants on both platforms."""
    try:
        parent = psutil.Process(pid)
        processes = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    # The subprocess owner must reap the root (Popen/asyncio owns its exit code).
    psutil.wait_procs(processes[:-1], timeout=3)


def sync_directory(path: Path) -> None:
    # Windows does not support opening directories for fsync. Replacement still
    # provides atomic visibility there; storage hardware must honour file flushes.
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def atomic_copy(source: Path, target: Path, *, temporary: Path | None = None) -> None:
    """Promote large databases without loading the entire file into memory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if temporary is None:
        descriptor, temporary = tempfile.mkstemp(prefix=f'.{target.name}-', dir=target.parent)
    else:
        if temporary.parent != target.parent:
            raise ValueError('Atomic copy temporary must share the destination directory')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output, source.open('rb') as input_stream:
            shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ProcessLock:
    """An OS-owned lock, never a PID file or stale-lock timeout.

    Keep the lock file permanently: unlinking a locked inode allows a second
    process to acquire a different lock under the same filename on Unix.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.stream.write(b'\0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise LockUnavailable(f'Another updater owns {self.path.name}') from exc
            raise
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
