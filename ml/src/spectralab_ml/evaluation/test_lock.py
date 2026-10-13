"""Consume a held-out test once, before parsing or scoring its spectra.

Create the receipt's parent directory before calling ``consume_test``. An
exclusive create consumes the test even if the caller later crashes or fails.
An explicit replay may only evaluate the exact recorded inputs and models;
it never becomes a new untouched evaluation. Keep this receipt permanently.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_RECEIPT_BYTES = 262_144
_IDENTITY_FIELDS = {
    "test_content_hash",
    "protocol_hash",
    "profile_identity",
    "baseline_sha256",
    "implementation_sha256",
}
_RECEIPT_FIELDS = {"schema_version", "consumed_at", "identity", "evaluation_kind"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _identity(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != _IDENTITY_FIELDS:
        raise ValueError("Complete test, protocol, model and implementation identity required")
    sources = value["implementation_sha256"]
    if (
        not isinstance(sources, dict)
        or not 1 <= len(sources) <= 1024
        or any(not isinstance(key, str) or not 1 <= len(key) <= 1024 for key in sources)
    ):
        raise ValueError("Nonempty bounded implementation checksum map required")
    hashes = [value[key] for key in _IDENTITY_FIELDS - {"implementation_sha256"}]
    hashes.extend(sources.values())
    if any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item) for item in hashes):
        raise ValueError("Test identity must use complete lowercase SHA-256 checksums")
    raw = _json_bytes(value)
    if len(raw) > MAX_RECEIPT_BYTES - 512:
        raise ValueError("Test identity exceeds the receipt size limit")
    return json.loads(raw)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field in test-consumption receipt")
        result[key] = value
    return result


def _read_receipt(path: Path) -> dict:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RECEIPT_BYTES:
            raise ValueError("Test-consumption receipt must be a bounded regular JSON file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            raise ValueError("Test-consumption receipt exceeds the size limit")
        receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (
            not isinstance(receipt, dict)
            or set(receipt) != _RECEIPT_FIELDS
            or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1
            or receipt["evaluation_kind"] != "first_locked_evaluation"
            or not isinstance(receipt["consumed_at"], str)
            or datetime.fromisoformat(receipt["consumed_at"]).utcoffset() is None
        ):
            raise ValueError("Invalid test-consumption receipt schema")
        receipt["identity"] = _identity(receipt["identity"])
        return receipt
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError(
            "Unreadable or corrupt test-consumption receipt; test remains consumed"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def consume_test(path: Path | str, identity: dict, *, replay: bool = False) -> dict:
    """Persist a consumption receipt, or admit an explicit identical locked replay.

    Call immediately before the first test read, after model artifacts are durable.
    Existing, partial and corrupt receipts are never replaced or removed. A failed
    write leaves the receipt in place and must not be followed by test access.
    """
    if not isinstance(replay, bool):
        raise ValueError("replay must be an explicit boolean")
    pinned = _identity(identity)
    path = Path(path)
    receipt = {
        "schema_version": 1,
        "consumed_at": datetime.now(UTC).isoformat(),
        "identity": pinned,
        "evaluation_kind": "first_locked_evaluation",
    }
    raw = _json_bytes(receipt) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        if not replay:
            raise ValueError(
                "Held-out test already consumed; an explicit locked replay is required"
            ) from exc
        saved = _read_receipt(path)
        if _json_bytes(saved["identity"]) != _json_bytes(pinned):
            raise ValueError(
                "Locked replay identity differs from the consumed test receipt"
            ) from exc
        return {**saved, "evaluation_kind": "locked_replay"}
    # Fsync the directory even after a failed write: a partial receipt also consumes
    # the test. Never remove it in an exception handler or turn it into a retry.
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    return receipt
