from types import SimpleNamespace

import pytest

from edgar_moe.modeling import frozen


def test_frozen_publish_cleans_unpublished_staging_on_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(frozen, "_write_frozen_evaluation", fail_write)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        frozen.save_frozen_evaluation(SimpleNamespace(dataset_id="dataset-1"), tmp_path)

    assert not (tmp_path / "dataset-1").exists()
    assert not list(tmp_path.glob(".dataset-1.staging-*"))
