"""SAM and micro-SAM segmentation for bioimage nuclei detection.

Two entrypoints:
  - segment_sam()       — Meta's Segment Anything Model (automatic mask generation)
  - segment_microsam()  — micro-SAM, SAM fine-tuned for fluorescence / EM microscopy

Both return a labeled int32 mask identical in format to segment_watershed().

Install (SAM):
    pip install segment-anything
    wget https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth

Install (micro-SAM):
    pip install micro-sam
    (checkpoints download automatically on first use)
"""

import numpy as np

from src.preprocessing import normalize_image


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_rgb_uint8(image: np.ndarray) -> np.ndarray:
    """Convert any image array to HxWx3 uint8 for SAM."""
    if image.ndim == 2:
        rgb = np.stack([image, image, image], axis=-1)
    elif image.ndim == 3 and image.shape[2] == 1:
        rgb = np.concatenate([image, image, image], axis=-1)
    elif image.ndim == 3 and image.shape[2] >= 3:
        rgb = image[:, :, :3]
    else:
        rgb = image

    if rgb.dtype != np.uint8:
        rgb = (normalize_image(rgb.astype(np.float64)) * 255).astype(np.uint8)
    return rgb


def _masks_to_labeled(masks_list: list, shape: tuple[int, int]) -> np.ndarray:
    """Convert SAM mask-dict list to a labeled int32 array.

    Masks sorted by area descending so smaller objects (painted last) are not
    swallowed by large neighbours.
    """
    labeled = np.zeros(shape, dtype=np.int32)
    if not masks_list:
        return labeled
    for idx, m in enumerate(
        sorted(masks_list, key=lambda x: x["area"], reverse=True), start=1
    ):
        labeled[m["segmentation"]] = idx
    return labeled


# ── SAM ───────────────────────────────────────────────────────────────────────

def segment_sam(
    image: np.ndarray,
    checkpoint_path: str | None = None,
    model_type: str = "vit_b",
    points_per_side: int = 32,
    pred_iou_thresh: float = 0.88,
    stability_score_thresh: float = 0.95,
    min_mask_area: int = 50,
) -> np.ndarray:
    """Segment nuclei using Meta's Segment Anything Model.

    Uses SamAutomaticMaskGenerator — SAM places a regular grid of point prompts
    and generates instance masks for every detected object without explicit user
    prompts.  Well-suited for dense nuclei imagery.

    Args:
        image: Input image (any shape/dtype; converted to RGB uint8 internally).
        checkpoint_path: Path to a SAM .pth checkpoint.  Download with:
            wget https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
        model_type: One of 'vit_b', 'vit_l', 'vit_h'.
            vit_b  ~  375 MB  (fastest, good accuracy)
            vit_l  ~  1.2 GB
            vit_h  ~  2.6 GB  (highest accuracy)
        points_per_side: Grid density for automatic prompting.
            Higher values catch small nuclei but increase runtime.
        pred_iou_thresh: Per-mask IoU quality filter (0–1).
        stability_score_thresh: Mask stability filter (0–1).
        min_mask_area: Discard masks smaller than this many pixels.

    Returns:
        Labeled int32 mask — 0 = background, 1…N = individual nuclei.

    Raises:
        ImportError: If segment_anything or torch is not installed.
        ValueError: If checkpoint_path is None.
    """
    try:
        import torch
        from segment_anything import sam_model_registry, SamAutomaticMaskGenerator
    except ImportError as exc:
        raise ImportError(
            "segment_anything is not installed. Install it with:\n"
            "  pip install segment-anything\n"
            "Then download a checkpoint:\n"
            "  wget https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth\n"
            "Source: https://github.com/facebookresearch/segment-anything"
        ) from exc

    if checkpoint_path is None:
        raise ValueError(
            "SAM requires a model checkpoint file. Download one with:\n"
            "  wget https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth\n"
            "Then pass checkpoint_path='sam_vit_b_01ec64.pth'."
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"

    sam = sam_model_registry[model_type](checkpoint=checkpoint_path)
    sam.to(device)
    sam.eval()

    generator = SamAutomaticMaskGenerator(
        sam,
        points_per_side=points_per_side,
        pred_iou_thresh=pred_iou_thresh,
        stability_score_thresh=stability_score_thresh,
        min_mask_region_area=min_mask_area,
    )

    rgb = _to_rgb_uint8(image)
    h, w = rgb.shape[:2]

    with torch.no_grad():
        masks = generator.generate(rgb)

    return _masks_to_labeled(masks, (h, w))


# ── micro-SAM ─────────────────────────────────────────────────────────────────

def segment_microsam(
    image: np.ndarray,
    model_type: str = "vit_b_lm",
    checkpoint_path: str | None = None,
    min_mask_area: int = 50,
) -> np.ndarray:
    """Segment using micro-SAM — SAM fine-tuned for light microscopy.

    micro-SAM checkpoints are downloaded automatically the first time and cached
    in ~/.cache/micro_sam/.  No manual download required.

    Recommended model types for fluorescence nuclei:
        'vit_b_lm'              — fast, light-microscopy fine-tune (~375 MB)
        'vit_l_lm'              — higher accuracy, heavier
        'vit_b_em_organelles'   — electron microscopy organelles

    Args:
        image: Input image (any shape/dtype).
        model_type: micro-SAM model identifier (see above).
        checkpoint_path: Optional path to a custom checkpoint. None = auto-download.
        min_mask_area: Discard objects smaller than this many pixels.

    Returns:
        Labeled int32 mask — 0 = background, 1…N = individual objects.

    Raises:
        ImportError: If micro_sam is not installed.
    """
    try:
        import micro_sam.automatic_segmentation as auto_seg
        from micro_sam.util import get_sam_model
        from micro_sam.instance_segmentation import AutomaticMaskGenerator
    except ImportError as exc:
        raise ImportError(
            "micro_sam is not installed. Install it with:\n"
            "  pip install micro-sam\n"
            "See: https://github.com/computational-cell-analytics/micro-sam"
        ) from exc

    rgb = _to_rgb_uint8(image)

    predictor = get_sam_model(model_type=model_type, checkpoint_path=checkpoint_path)
    segmenter = AutomaticMaskGenerator(predictor)

    # ndim=2 forces 2D processing; verbose=False suppresses per-slice progress bars
    result = auto_seg.automatic_instance_segmentation(
        predictor, segmenter, input_path=rgb, ndim=2, verbose=False
    )

    if isinstance(result, np.ndarray):
        labeled = result.astype(np.int32)
    else:
        labeled = _masks_to_labeled(result, rgb.shape[:2])

    # Remove objects below min_mask_area
    if min_mask_area > 0:
        from skimage.morphology import remove_small_objects
        from scipy.ndimage import label as nd_label
        binary = labeled > 0
        binary = remove_small_objects(binary, min_size=min_mask_area)
        labeled, _ = nd_label(binary)
        labeled = labeled.astype(np.int32)

    return labeled
