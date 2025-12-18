import numpy as np
import pytest
from src.evaluate import dice_score, iou_score, count_objects, object_count_error, evaluate_segmentation


def _make_mask(val=1):
    m = np.zeros((10, 10), dtype=int)
    m[2:5, 2:5] = val
    return m


def test_perfect_dice():
    gt = _make_mask()
    assert dice_score(gt, gt.copy()) == 1.0


def test_perfect_iou():
    gt = _make_mask()
    assert iou_score(gt, gt.copy()) == 1.0


def test_no_overlap_dice():
    gt = np.zeros((10, 10), dtype=int)
    pred = np.zeros((10, 10), dtype=int)
    gt[0:3, 0:3] = 1
    pred[7:10, 7:10] = 1
    assert dice_score(gt, pred) == 0.0


def test_no_overlap_iou():
    gt = np.zeros((10, 10), dtype=int)
    pred = np.zeros((10, 10), dtype=int)
    gt[0:3, 0:3] = 1
    pred[7:10, 7:10] = 1
    assert iou_score(gt, pred) == 0.0


def test_empty_masks():
    empty = np.zeros((10, 10), dtype=int)
    assert dice_score(empty, empty) == 1.0
    assert iou_score(empty, empty) == 1.0


def test_object_count_error():
    gt = np.zeros((20, 20), dtype=int)
    pred = np.zeros((20, 20), dtype=int)
    gt[0:4, 0:4] = 1
    gt[10:14, 10:14] = 2
    pred[0:4, 0:4] = 1
    assert object_count_error(gt, pred) == 1


def test_evaluate_segmentation_keys():
    gt = _make_mask()
    result = evaluate_segmentation(gt, gt.copy())
    assert "dice" in result
    assert "iou" in result
    assert "num_objects_gt" in result
    assert "num_objects_pred" in result
    assert "object_count_error" in result
