"""Content-addressed cache for raw downloads.

Objects live at ``<root>/objects/<sha256[:2]>/<sha256>`` and are immutable. The store's
``source_fetch`` table maps URLs and providers to hashes so a download can be traced back to
exact bytes and re-used without hitting the network again.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedObject:
    sha256: str
    path: Path
    size: int


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.tmp = root / "tmp"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def path_for(self, sha256: str) -> Path:
        return self.objects / sha256[:2] / sha256

    def has(self, sha256: str) -> bool:
        return self.path_for(sha256).exists()

    def get(self, sha256: str) -> CachedObject | None:
        path = self.path_for(sha256)
        if not path.exists():
            return None
        return CachedObject(sha256, path, path.stat().st_size)

    def put_file(self, src: Path, move: bool = True) -> CachedObject:
        sha = self.hash_file(src)
        dest = self.path_for(sha)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            if move:
                shutil.move(str(src), str(dest))
            else:
                shutil.copy2(src, dest)
        elif move:
            src.unlink(missing_ok=True)
        return CachedObject(sha, dest, dest.stat().st_size)

    def put_bytes(self, data: bytes) -> CachedObject:
        sha = hashlib.sha256(data).hexdigest()
        dest = self.path_for(sha)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(data)
        return CachedObject(sha, dest, len(data))

    def temp_path(self, suffix: str = "") -> Path:
        return self.tmp / f"{time.time_ns()}{suffix}"

    def stats(self) -> tuple[int, int]:
        """Return (object count, total bytes)."""
        count = 0
        total = 0
        for path in self.objects.rglob("*"):
            if path.is_file():
                count += 1
                total += path.stat().st_size
        return count, total

    def prune(self, older_than_s: float) -> int:
        """Delete objects not accessed within *older_than_s* seconds. Returns count removed."""
        cutoff = time.time() - older_than_s
        removed = 0
        for path in self.objects.rglob("*"):
            if path.is_file() and path.stat().st_atime < cutoff:
                path.unlink()
                removed += 1
        for path in self.tmp.iterdir():
            path.unlink(missing_ok=True)
        return removed
