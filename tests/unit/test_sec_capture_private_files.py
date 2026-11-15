"""Offline checks of the prospective capture's POSIX private-file boundary."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import capture_prospective_sec_index as capture

INDEX = (
    b"CIK|Company Name|Form Type|Date Filed|File Name\n"
    b"100|Synthetic|10-K|2026-09-01|edgar/data/100/0000000100-26-000001.txt\n"
)
NOW = datetime(2026, 10, 10, tzinfo=UTC)
FILES = ("master.idx", "filer-roster.json", "commitment.json")


@pytest.fixture
def saved(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    policy = capture.load_policy()
    policy["minimum_eligible_rows"] = 1
    commitment = capture.build_commitment(INDEX, policy, captured_at=NOW)
    root = tmp_path / "artifacts"
    destination = root / "capture"
    capture.save_capture(INDEX, commitment, destination, policy, root=root)
    return root, destination, policy, commitment


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "shared", "fifo", "directory", "empty"])
def test_rejects_unsafe_private_files(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]], name: str, kind: str
) -> None:
    root, destination, policy, _ = saved
    path = destination / name
    if kind == "shared":
        path.chmod(0o640)
    elif kind == "empty":
        path.write_bytes(b"")
    else:
        preserved = root / f"preserved-{name}"
        path.rename(preserved)
        if kind == "symlink":
            path.symlink_to(preserved)
        elif kind == "hardlink":
            os.link(preserved, path)
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        else:
            path.mkdir(mode=0o700)
    with pytest.raises(capture.CohortCaptureError):
        capture.verify_capture(destination, policy, root=root)


@pytest.mark.parametrize(
    ("name", "bound"),
    [
        ("master.idx", "_MAX_INDEX_BYTES"),
        ("filer-roster.json", "_MAX_ROSTER_BYTES"),
        ("commitment.json", "_MAX_COMMITMENT_BYTES"),
    ],
)
def test_rejects_oversized_file_before_read(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    bound: str,
) -> None:
    root, destination, policy, _ = saved
    monkeypatch.setattr(capture, bound, (destination / name).stat().st_size - 1)
    original_read = os.read
    target_inode = (destination / name).stat().st_ino

    def guarded_read(descriptor: int, maximum: int) -> bytes:
        assert os.fstat(descriptor).st_ino != target_inode, "oversized input was read"
        return original_read(descriptor, maximum)

    monkeypatch.setattr(capture.os, "read", guarded_read)
    with pytest.raises(capture.CohortCaptureError, match="byte bound"):
        capture.verify_capture(destination, policy, root=root)


def test_rejects_shared_directory_without_changing_existing_capture(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
) -> None:
    root, destination, policy, commitment = saved
    destination.chmod(0o750)
    with pytest.raises(capture.CohortCaptureError, match="owner-only"):
        capture.verify_capture(destination, policy, root=root)
    assert destination.stat().st_mode & 0o777 == 0o750
    destination.chmod(0o700)
    assert capture.verify_capture(destination, policy, root=root) == commitment


@pytest.mark.parametrize("outside", [False, True])
def test_directory_alias_rejected_by_reader_and_writer(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]], outside: bool
) -> None:
    root, destination, policy, commitment = saved
    alias = root / "alias"
    target = destination if not outside else root.parent / "external"
    if outside:
        target.mkdir(mode=0o700)
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(capture.CohortCaptureError):
        capture.verify_capture(alias, policy, root=root)
    with pytest.raises(capture.CohortCaptureError):
        capture.save_capture(INDEX, commitment, alias / "new", policy, root=root)
    assert not (target / "new").exists()
    assert capture.verify_capture(destination, policy, root=root) == commitment


def test_refuses_root_and_parent_traversal(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
) -> None:
    root, _, policy, commitment = saved
    for destination in (root, root / ".." / "escaped"):
        with pytest.raises(capture.CohortCaptureError):
            capture.save_capture(INDEX, commitment, destination, policy, root=root)
    assert not (root.parent / "escaped").exists()


def test_writer_rejects_oversized_commitment_before_creating_directory(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
) -> None:
    root, _, policy, _ = saved
    policy = {**policy, "study_id": "x" * capture._MAX_COMMITMENT_BYTES}
    commitment = capture.build_commitment(INDEX, policy, captured_at=NOW)
    destination = root / "oversized"
    with pytest.raises(capture.CohortCaptureError, match="byte bound"):
        capture.save_capture(INDEX, commitment, destination, policy, root=root)
    assert not destination.exists()


def test_nested_new_capture_is_private_and_preserves_identity(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
) -> None:
    root, _, policy, commitment = saved
    destination = root / "nested" / "new"
    capture.save_capture(INDEX, commitment, destination, policy, root=root)
    assert (root / "nested").stat().st_mode & 0o777 == 0o700
    assert destination.stat().st_mode & 0o777 == 0o700
    assert capture.verify_capture(destination, policy, root=root) == commitment


@pytest.mark.parametrize("change", ["same-size", "growth"])
def test_read_rejects_concurrent_mutation(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    root, destination, policy, _ = saved
    path = destination / "master.idx"
    target_inode = path.stat().st_ino
    original_read = os.read
    mutated = False
    monkeypatch.setattr(capture, "_MAX_INDEX_BYTES", len(INDEX))

    def changing_read(descriptor: int, maximum: int) -> bytes:
        nonlocal mutated
        result = original_read(descriptor, maximum)
        if os.fstat(descriptor).st_ino == target_inode and not mutated:
            mutated = True
            if change == "growth":
                with path.open("ab") as output:
                    output.write(b"unexpected growth")
            else:
                path.write_bytes(INDEX.replace(b"Synthetic", b"Different"))
        return result

    monkeypatch.setattr(capture.os, "read", changing_read)
    with pytest.raises(capture.CohortCaptureError, match="changed while reading"):
        capture.verify_capture(destination, policy, root=root)
    assert mutated


@pytest.mark.parametrize("kind", ["existing", "dangling", "parent-alias", "outside", "root"])
def test_live_destination_preflight_does_not_contact_provider(
    saved: tuple[Path, Path, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, destination, policy, _ = saved
    output = destination
    if kind == "dangling":
        output = root / "dangling"
        output.symlink_to(root / "missing")
    elif kind == "parent-alias":
        alias = root / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        output = alias / "new"
    elif kind == "outside":
        output = root.parent / "outside"
    elif kind == "root":
        output = root
    monkeypatch.setattr(capture, "_ARTIFACT_ROOT", root)
    monkeypatch.setattr(capture, "load_policy", lambda: policy)
    monkeypatch.setattr(capture, "_check_time", lambda *_: None)
    monkeypatch.setattr(capture.sys, "argv", ["capture", "--live", "--output-dir", str(output)])
    monkeypatch.setenv("SEC_USER_AGENT", "Synthetic test test@example.invalid")

    def no_provider(*_: Any, **__: Any) -> None:
        pytest.fail("invalid output destination contacted the provider")

    monkeypatch.setattr(capture.httpx, "Client", no_provider)
    assert capture.main() == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert str(output) not in result.err
    assert not (destination / "new").exists()
