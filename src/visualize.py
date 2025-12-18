from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from skimage.segmentation import find_boundaries

from src.preprocessing import to_grayscale, normalize_image


def create_overlay(
    image: np.ndarray,
    pred_mask: np.ndarray,
    gt_mask: np.ndarray | None = None,
) -> np.ndarray:
    gray = normalize_image(to_grayscale(image))
    rgb = np.stack([gray, gray, gray], axis=-1)

    pred_bounds = find_boundaries(pred_mask, mode="outer")
    rgb[pred_bounds] = [1.0, 0.2, 0.2]  # red for predicted

    if gt_mask is not None:
        gt_bounds = find_boundaries(gt_mask > 0, mode="outer")
        rgb[gt_bounds] = [0.2, 1.0, 0.2]  # green for ground truth

    return (rgb * 255).astype(np.uint8)


def save_overlay(
    image: np.ndarray,
    pred_mask: np.ndarray,
    gt_mask: np.ndarray | None,
    output_path: str | Path,
    title: str | None = None,
) -> None:
    output_path = Path(output_path)
    overlay = create_overlay(image, pred_mask, gt_mask)

    n_cols = 3 if gt_mask is not None else 2
    fig, axes = plt.subplots(1, n_cols, figsize=(5 * n_cols, 5))

    axes[0].imshow(normalize_image(to_grayscale(image)), cmap="gray")
    axes[0].set_title("Image")
    axes[0].axis("off")

    if gt_mask is not None:
        axes[1].imshow(gt_mask, cmap="nipy_spectral", interpolation="nearest")
        axes[1].set_title("Ground Truth")
        axes[1].axis("off")
        axes[2].imshow(overlay)
        axes[2].set_title("Overlay (red=pred, green=GT)")
        axes[2].axis("off")
    else:
        axes[1].imshow(overlay)
        axes[1].set_title("Overlay (red=pred)")
        axes[1].axis("off")

    if title:
        fig.suptitle(title, fontsize=12)

    plt.tight_layout()
    plt.savefig(output_path, dpi=100, bbox_inches="tight")
    plt.close(fig)
