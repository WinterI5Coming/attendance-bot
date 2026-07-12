"""Single-instance lock implemented with an exclusive lock file."""

from __future__ import annotations

from pathlib import Path
import os
import sys
from types import TracebackType
from typing import Self


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InstanceLock:
    """Prevent multiple copies of the bot from running in one app directory."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._handle = None

    def acquire(self) -> bool:
        """Try to acquire the lock without blocking."""

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.lock_path.open("a+", encoding="utf-8")
        self._handle.seek(0)

        try:
            if os.name == "nt":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._handle.close()
            self._handle = None
            return False

        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return True

    def release(self) -> None:
        """Release the lock and close the lock file handle."""

        if self._handle is None:
            return

        try:
            self._handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        if not self.acquire():
            raise RuntimeError(
                "Discord 출석 봇이 이미 실행 중입니다.\n"
                "기존 프로그램을 종료한 후 다시 실행해주세요."
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
