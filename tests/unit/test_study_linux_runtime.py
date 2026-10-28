"""The Linux CI owner fixture must reject non-CI and indirect targets before I/O."""

import importlib.util
from pathlib import Path

import pytest

PATH = (
    Path(__file__).resolve().parents[2]
    / "ops/private-read-admission/study-prep-20261027/linux-runtime-v1/fixture.py"
)
SPEC = importlib.util.spec_from_file_location("isolated_linux_study_fixture", PATH)
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


@pytest.mark.parametrize(
    "fault", ["platform", "actions", "runner", "container", "existing", "outside", "alias"]
)
def test_non_ci_or_indirect_fixture_refuses_before_container_or_database_io(
    tmp_path, monkeypatch, fault
):
    monkeypatch.setattr(fixture, "ROOT", tmp_path)
    monkeypatch.setattr(fixture.sys, "platform", "linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    bundle = tmp_path / "data/artifacts/bundle"
    bundle.mkdir(parents=True)
    output = bundle.parent / "output"
    container = "a" * 64
    if fault == "platform":
        monkeypatch.setattr(fixture.sys, "platform", "darwin")
    elif fault == "actions":
        monkeypatch.setenv("GITHUB_ACTIONS", "false")
    elif fault == "runner":
        monkeypatch.setenv("RUNNER_OS", "macOS")
    elif fault == "container":
        container = "a; remote-command"
    elif fault == "existing":
        output.mkdir()
    elif fault == "outside":
        output = tmp_path / "foreign"
    else:
        alias = bundle.parent / "alias"
        alias.symlink_to(bundle, target_is_directory=True)
        bundle = alias
    monkeypatch.setattr(
        fixture.subprocess, "run", lambda *_a, **_k: pytest.fail("container operation reached")
    )
    with pytest.raises(ValueError, match="^fresh_isolated_linux_ci_fixture_required$"):
        fixture.run(bundle, output, container)


def test_guard_accepts_only_the_explicit_generated_ci_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, "ROOT", tmp_path)
    monkeypatch.setattr(fixture.sys, "platform", "linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    bundle = tmp_path / "data/artifacts/bundle"
    bundle.mkdir(parents=True)
    assert fixture.check_scope(bundle, bundle.parent / "new", "a" * 64) is None


def test_failed_baseline_query_rolls_back_its_transaction(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "snapshot_kernel",
        SimpleNamespace(CONTROL_SQL=("SET LOCAL search_path=pg_catalog",)),
    )
    commands = []

    class Connection:
        def execute(self, command):
            commands.append(command)
            if command == "schema":
                raise ValueError("query failed")

    with pytest.raises(ValueError, match="query failed"):
        fixture.capture(Connection(), {"schema": "schema", "fixture": "rows"})
    assert commands[-1] == "ROLLBACK"
