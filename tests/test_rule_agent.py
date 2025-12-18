import pytest
from src.rule_agent import analyze_segmentation

_default_params = {"sigma": 1.0, "min_size": 30, "min_distance": 5, "use_contrast_enhancement": False}
_pass_qc = {"tiny_object_fraction": 0.0, "is_blurry": False, "is_low_contrast": False, "has_many_tiny_objects": False}


def test_pass_on_good_result():
    metrics = {"dice": 0.85, "iou": 0.75, "num_objects_gt": 10, "num_objects_pred": 10, "object_count_error": 0}
    result = analyze_segmentation(metrics, _pass_qc, _default_params)
    assert result["status"] == "pass"
    assert result["should_rerun"] is False


def test_over_segmentation_increases_sigma_and_min_size():
    metrics = {"dice": 0.4, "iou": 0.3, "num_objects_gt": 10, "num_objects_pred": 20, "object_count_error": 10}
    result = analyze_segmentation(metrics, _pass_qc, _default_params)
    assert result["status"] == "needs_review"
    assert "over-segmentation" in result["failure_mode"]
    assert result["new_params"]["sigma"] > _default_params["sigma"]
    assert result["new_params"]["min_size"] > _default_params["min_size"]


def test_under_segmentation_decreases_sigma_and_min_size():
    metrics = {"dice": 0.4, "iou": 0.3, "num_objects_gt": 20, "num_objects_pred": 5, "object_count_error": 15}
    result = analyze_segmentation(metrics, _pass_qc, _default_params)
    assert result["status"] == "needs_review"
    assert "under-segmentation" in result["failure_mode"]
    assert result["new_params"]["sigma"] < _default_params["sigma"]
    assert result["new_params"]["min_size"] < _default_params["min_size"]


def test_low_contrast_enables_enhancement():
    metrics = {"dice": 0.4, "iou": 0.3, "num_objects_gt": 10, "num_objects_pred": 10, "object_count_error": 0}
    qc = {**_pass_qc, "is_low_contrast": True}
    result = analyze_segmentation(metrics, qc, _default_params)
    assert result["new_params"].get("use_contrast_enhancement") is True


def test_blurry_image_triggers_review():
    metrics = {"dice": 0.3, "iou": 0.2, "num_objects_gt": 10, "num_objects_pred": 10, "object_count_error": 0}
    qc = {**_pass_qc, "is_blurry": True}
    result = analyze_segmentation(metrics, qc, _default_params)
    assert result["status"] == "needs_review"
    assert "blurry image" in result["failure_mode"]
