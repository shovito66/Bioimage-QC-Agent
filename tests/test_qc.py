import numpy as np
import pytest
from src.qc import blur_score, contrast_score, tiny_object_fraction, run_qc


def _sharp_image():
    img = np.zeros((50, 50), dtype=np.float64)
    img[20:30, 20:30] = 1.0
    return img


def _blurry_image():
    from scipy.ndimage import gaussian_filter
    img = _sharp_image()
    return gaussian_filter(img, sigma=5)


def _low_contrast_image():
    return np.full((50, 50), 0.5, dtype=np.float64)


def test_sharp_has_higher_blur_score_than_blurry():
    sharp = blur_score(_sharp_image())
    blurry = blur_score(_blurry_image())
    assert sharp > blurry


def test_low_contrast_flagged():
    img = _low_contrast_image()
    assert contrast_score(img) < 0.05


def test_normal_contrast_not_flagged():
    img = _sharp_image()
    assert contrast_score(img) >= 0.05


def test_tiny_object_fraction():
    mask = np.zeros((50, 50), dtype=int)
    mask[0:2, 0:2] = 1    # 4 px — tiny
    mask[10:20, 10:20] = 2  # 100 px — not tiny
    frac = tiny_object_fraction(mask, min_area=20)
    assert frac == pytest.approx(0.5)


def test_run_qc_keys():
    img = _sharp_image()
    mask = np.zeros((50, 50), dtype=int)
    mask[10:20, 10:20] = 1
    result = run_qc(img, mask)
    expected = {"blur_score", "contrast_score", "tiny_object_fraction",
                "num_objects_pred", "median_object_area",
                "is_blurry", "is_low_contrast", "has_many_tiny_objects"}
    assert expected.issubset(result.keys())
