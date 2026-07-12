"""Local content-addressed storage with staging, fsync, and atomic publication."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from research_forge.domain.artifact import ArtifactRef
from research_forge.domain.errors import ArtifactIntegrityViolation, PathSafetyViolation


class LocalContentAddressedStore:
    """Store bytes as ``cas/<sha256>`` without allowing mutable artifact paths."""

    def __init__(self, root_path: Path) -> None:
        self._root = root_path.resolve()
        self._cas_path = self._root / "cas"
        self._staging_path = self._root / "staging"
        self._cas_path.mkdir(parents=True, exist_ok=True)
        self._staging_path.mkdir(parents=True, exist_ok=True)

    def put(self, payload: bytes, *, media_type: str) -> ArtifactRef:
        sha256 = hashlib.sha256(payload).hexdigest()
        reference = ArtifactRef(sha256=sha256, size_bytes=len(payload), media_type=media_type)
        destination = self._safe_blob_path(sha256)
        if destination.exists():
            self.read_verified(reference)
            return reference

        descriptor, staging_name = tempfile.mkstemp(prefix="artifact-", dir=self._staging_path)
        staging_path = Path(staging_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if destination.exists():
                self.read_verified(reference)
            else:
                os.replace(staging_path, destination)
            self.read_verified(reference)
        finally:
            if staging_path.exists():
                staging_path.unlink()
        return reference

    def read_verified(self, reference: ArtifactRef) -> bytes:
        blob_path = self._safe_blob_path(reference.sha256)
        if not blob_path.is_file() or blob_path.is_symlink():
            raise ArtifactIntegrityViolation(f"CAS blob is missing or unsafe: {reference.sha256}")
        payload = blob_path.read_bytes()
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != reference.sha256 or len(payload) != reference.size_bytes:
            raise ArtifactIntegrityViolation(f"CAS blob failed integrity check: {reference.sha256}")
        return payload

    def verify(self, reference: ArtifactRef) -> bool:
        try:
            self.read_verified(reference)
        except ArtifactIntegrityViolation:
            return False
        return True

    def collect_orphans(self, *, referenced_sha256: set[str], minimum_age: timedelta) -> tuple[str, ...]:
        """Delete only aged, unregistered regular blobs; callers derive references from durable DB state."""
        normalized_references = {value.lower() for value in referenced_sha256}
        cutoff = time.time() - minimum_age.total_seconds()
        removed: list[str] = []
        for candidate in self._cas_path.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.name in normalized_references or candidate.stat().st_mtime > cutoff:
                continue
            try:
                self._safe_blob_path(candidate.name)
            except PathSafetyViolation:
                continue
            candidate.unlink()
            removed.append(candidate.name)
        return tuple(sorted(removed))

    def _safe_blob_path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise PathSafetyViolation("CAS digest must be a lowercase SHA-256 hex digest.")
        blob_path = self._cas_path / sha256
        if blob_path.parent.resolve() != self._cas_path:
            raise PathSafetyViolation("CAS blob path escapes the CAS root.")
        return blob_path
