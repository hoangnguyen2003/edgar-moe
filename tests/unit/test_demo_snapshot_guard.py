from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import orjson
import pytest

from edgar_moe.data import demo


def _fail_if_training_starts(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the guard must run before any synthetic training")


def test_demo_refuses_to_overwrite_the_locked_public_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked = tmp_path / "snapshot.json"
    shutil.copyfile("data/demo/snapshot.json", locked)
    digest = hashlib.sha256(locked.read_bytes()).hexdigest()
    monkeypatch.setattr(demo, "generate_synthetic_dataset", _fail_if_training_starts)

    with pytest.raises(FileExistsError, match="non-synthetic"):
        demo.build_demo_snapshot(locked)

    assert hashlib.sha256(locked.read_bytes()).hexdigest() == digest


def test_demo_refuses_to_overwrite_an_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "notes.json"
    target.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(demo, "generate_synthetic_dataset", _fail_if_training_starts)

    with pytest.raises(FileExistsError):
        demo.build_demo_snapshot(target)

    assert target.read_text(encoding="utf-8") == "not json"


def test_demo_may_replace_an_earlier_synthetic_fixture(tmp_path: Path) -> None:
    earlier = tmp_path / "synthetic.json"
    earlier.write_bytes(orjson.dumps({"metadata": {"data_mode": "synthetic_fixture"}}))

    demo.require_replaceable_demo_output(earlier)
    demo.require_replaceable_demo_output(tmp_path / "missing.json")
