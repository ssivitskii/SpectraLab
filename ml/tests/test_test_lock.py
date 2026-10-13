from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from spectralab_ml.evaluation.test_lock import MAX_RECEIPT_BYTES, consume_test


@pytest.fixture
def identity():
    def sha(value):
        return hashlib.sha256(value.encode()).hexdigest()

    return {
        "test_content_hash": sha("test"),
        "protocol_hash": sha("protocol"),
        "profile_identity": sha("profile"),
        "baseline_sha256": sha("baseline"),
        "implementation_sha256": {"runner.py": sha("runner"), "model.py": sha("model")},
    }


def test_first_consumption_is_durable_and_duplicate_is_rejected(tmp_path, identity, monkeypatch):
    path = tmp_path / "consumed.json"
    syncs = []
    real_fsync = os.fsync

    def fsync(descriptor):
        syncs.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    receipt = consume_test(path, identity)
    assert receipt["evaluation_kind"] == "first_locked_evaluation"
    assert receipt["identity"] == identity
    assert datetime.fromisoformat(receipt["consumed_at"]).utcoffset() is not None
    assert json.loads(path.read_text()) == receipt
    assert syncs == [stat.S_IFREG, stat.S_IFDIR]
    original = path.read_bytes()
    with pytest.raises(ValueError, match="already consumed"):
        consume_test(path, identity)
    assert path.read_bytes() == original


def test_identical_replay_does_not_mutate_receipt(tmp_path, identity):
    path = tmp_path / "consumed.json"
    first = consume_test(path, identity)
    original, modified = path.read_bytes(), path.stat().st_mtime_ns
    reordered = dict(reversed(list(identity.items())))
    replay = consume_test(path, reordered, replay=True)
    assert replay == {**first, "evaluation_kind": "locked_replay"}
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize(
    "field",
    [
        "test_content_hash",
        "protocol_hash",
        "profile_identity",
        "baseline_sha256",
        "implementation_sha256",
    ],
)
def test_replay_requires_every_pinned_identity_field(tmp_path, identity, field):
    path = tmp_path / "consumed.json"
    consume_test(path, identity)
    changed = copy.deepcopy(identity)
    if field == "implementation_sha256":
        changed[field]["runner.py"] = "a" * 64
    else:
        changed[field] = "a" * 64
    with pytest.raises(ValueError, match="identity differs"):
        consume_test(path, changed, replay=True)


@pytest.mark.parametrize("content", [b"", b"{", b"null", b"\xff", b"x" * (MAX_RECEIPT_BYTES + 1)])
def test_partial_corrupt_or_oversized_receipt_is_never_reused(tmp_path, identity, content):
    path = tmp_path / "consumed.json"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="remains consumed"):
        consume_test(path, identity, replay=True)
    assert path.read_bytes() == content
    with pytest.raises(ValueError, match="already consumed"):
        consume_test(path, identity)


@pytest.mark.parametrize(
    "change",
    ["boolean_version", "missing_field", "bad_timestamp", "duplicate_field", "replay_receipt"],
)
def test_malformed_receipt_schema_is_not_replayable(tmp_path, identity, change):
    path = tmp_path / "consumed.json"
    receipt = consume_test(path, identity)
    if change == "boolean_version":
        receipt["schema_version"] = True
    elif change == "missing_field":
        del receipt["identity"]["protocol_hash"]
    elif change == "bad_timestamp":
        receipt["consumed_at"] = "2026-10-13"
    elif change == "replay_receipt":
        receipt["evaluation_kind"] = "locked_replay"
    raw = json.dumps(receipt)
    if change == "duplicate_field":
        raw = '{"schema_version":1,' + raw[1:]
    path.write_text(raw)
    with pytest.raises(ValueError, match="remains consumed"):
        consume_test(path, identity, replay=True)


def test_symlink_receipt_is_not_followed(tmp_path, identity):
    target, path = tmp_path / "target.json", tmp_path / "consumed.json"
    consume_test(target, identity)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="remains consumed"):
        consume_test(path, identity, replay=True)


def test_competing_first_calls_only_admit_one(tmp_path, identity):
    path = tmp_path / "consumed.json"

    def attempt():
        try:
            return consume_test(path, identity)["evaluation_kind"]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == ["first_locked_evaluation", "rejected"]


def test_failed_fsync_leaves_test_consumed(tmp_path, identity, monkeypatch):
    path = tmp_path / "consumed.json"
    real_fsync = os.fsync

    def fail_file_sync(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("simulated disk failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_file_sync)
    with pytest.raises(OSError, match="disk failure"):
        consume_test(path, identity)
    assert path.exists()
    with pytest.raises(ValueError, match="already consumed"):
        consume_test(path, identity)


def test_incomplete_or_oversized_identity_does_not_consume_test(tmp_path, identity):
    path = tmp_path / "consumed.json"
    for altered in ({}, {**identity, "protocol_hash": True}):
        with pytest.raises(ValueError):
            consume_test(path, altered)
        assert not path.exists()
    large = {
        **identity,
        "implementation_sha256": {"x" * 900 + str(i): "a" * 64 for i in range(300)},
    }
    with pytest.raises(ValueError, match="size limit"):
        consume_test(path, large)
    assert not path.exists()
