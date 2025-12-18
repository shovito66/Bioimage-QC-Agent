import numpy as np
from scipy.ndimage import label as nd_label

from src.preprocessing import to_grayscale, normalize_image


def blur_score(image: np.ndarray) -> float:
    gray = normalize_image(to_grayscale(image))
    laplacian = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    from scipy.ndimage import convolve
    response = convolve(gray, laplacian)
    return float(response.var())


def contrast_score(image: np.ndarray) -> float:
    gray = normalize_image(to_grayscale(image))
    return float(gray.std())


def object_area_stats(mask: np.ndarray) -> dict:
    if mask.max() == 0:
        return {"median_area": 0.0, "mean_area": 0.0, "num_objects": 0, "areas": []}
    if mask.max() == 1:
        labeled, _ = nd_label(mask > 0)
    else:
        labeled = mask

    areas = []
    for region_id in range(1, labeled.max() + 1):
        area = int((labeled == region_id).sum())
        if area > 0:
            areas.append(area)

    if not areas:
        return {"median_area": 0.0, "mean_area": 0.0, "num_objects": 0, "areas": []}

    return {
        "median_area": float(np.median(areas)),
        "mean_area": float(np.mean(areas)),
        "num_objects": len(areas),
        "areas": areas,
    }


def tiny_object_fraction(mask: np.ndarray, min_area: int = 20) -> float:
    stats = object_area_stats(mask)
    if stats["num_objects"] == 0:
        return 0.0
    n_tiny = sum(1 for a in stats["areas"] if a < min_area)
    return float(n_tiny / stats["num_objects"])


def run_qc(
    image: np.ndarray,
    pred_mask: np.ndarray,
    blur_threshold: float = 20.0,
    contrast_threshold: float = 0.05,
    tiny_object_threshold: float = 0.25,
    min_tiny_area: int = 20,
) -> dict:
    b = blur_score(image)
    c = contrast_score(image)
    tof = tiny_object_fraction(pred_mask, min_area=min_tiny_area)
    stats = object_area_stats(pred_mask)

    return {
        "blur_score": b,
        "contrast_score": c,
        "tiny_object_fraction": tof,
        "num_objects_pred": stats["num_objects"],
        "median_object_area": stats["median_area"],
        "is_blurry": b < blur_threshold,
        "is_low_contrast": c < contrast_threshold,
        "has_many_tiny_objects": tof > tiny_object_threshold,
    }
