import numpy as np

from edgar_moe.modeling.preprocess import MultimodalPreprocessor


def test_preprocessor_marks_fully_missing_modalities() -> None:
    train = {
        "text": np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]),
        "market": np.array([[1.0], [2.0], [3.0]]),
    }
    processor = MultimodalPreprocessor().fit(train)
    transformed, mask = processor.transform(
        {
            "text": np.array([[np.nan, np.nan], [7.0, 8.0]]),
            "market": np.array([[4.0], [np.nan]]),
        }
    )
    assert mask.tolist() == [[1.0, 0.0], [0.0, 1.0]]
    assert np.isfinite(transformed["text"]).all()


def test_validation_values_do_not_change_training_scaler() -> None:
    processor = MultimodalPreprocessor().fit({"x": np.array([[0.0], [2.0]])})
    transformed, _ = processor.transform({"x": np.array([[100.0]])})
    assert transformed["x"][0, 0] > 50
