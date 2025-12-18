import numpy as np
from scipy.ndimage import label as nd_label


def binarize_mask(mask: np.ndarray) -> np.ndarray:
    return mask > 0


def dice_score(gt_mask: np.ndarray, pred_mask: np.ndarray) -> float:
    gt = binarize_mask(gt_mask)
    pred = binarize_mask(pred_mask)
    intersection = np.logical_and(gt, pred).sum()
    denom = gt.sum() + pred.sum()
    if denom == 0:
        return 1.0
    return float(2 * intersection / denom)


def iou_score(gt_mask: np.ndarray, pred_mask: np.ndarray) -> float:
    gt = binarize_mask(gt_mask)
    pred = binarize_mask(pred_mask)
    intersection = np.logical_and(gt, pred).sum()
    union = np.logical_or(gt, pred).sum()
    if union == 0:
        return 1.0
    return float(intersection / union)


def count_objects(mask: np.ndarray) -> int:
    if mask.max() == 0:
        return 0
    if mask.max() == 1:
        labeled, n = nd_label(mask > 0)
        return n
    return int(mask.max())


def object_count_error(gt_mask: np.ndarray, pred_mask: np.ndarray) -> int:
    return abs(count_objects(gt_mask) - count_objects(pred_mask))


def evaluate_segmentation(gt_mask: np.ndarray, pred_mask: np.ndarray) -> dict:
    return {
        "dice": dice_score(gt_mask, pred_mask),
        "iou": iou_score(gt_mask, pred_mask),
        "num_objects_gt": count_objects(gt_mask),
        "num_objects_pred": count_objects(pred_mask),
        "object_count_error": object_count_error(gt_mask, pred_mask),
    }
