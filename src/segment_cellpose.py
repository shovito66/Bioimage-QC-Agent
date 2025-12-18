"""Cellpose-based nuclei segmentation.

Wraps Cellpose v2 (models.Cellpose) and v3+ (models.CellposeModel) transparently
so the same call works regardless of which version is installed.

Install:
    pip install cellpose          # CPU
    pip install 'cellpose[gui]'   # GPU / GUI extras
"""

import numpy as np

from src.preprocessing import to_grayscale, normalize_image


def _build_model(model_type: str, use_gpu: bool):
    """Return a Cellpose model object, handling v2 and v3 API differences."""
    from cellpose import models as cp_models

    # Cellpose v3+ uses CellposeModel; v2 used Cellpose
    if hasattr(cp_models, "CellposeModel"):
        # v3: pretrained_model maps 'nuclei' → 'nuclei_cp3' or similar
        # model_type kwarg is still accepted as an alias in v3
        return cp_models.CellposeModel(
            model_type=model_type,
            gpu=use_gpu,
        )
    elif hasattr(cp_models, "Cellpose"):
        # v2
        return cp_models.Cellpose(model_type=model_type, gpu=use_gpu)
    else:
        raise ImportError(
            "Could not find Cellpose model class. "
            "Try: pip install --upgrade cellpose"
        )


def _run_eval(model, img_input: np.ndarray, diameter, channels, min_size) -> np.ndarray:
    """Run model.eval() and return the labeled mask, handling v2/v3 return shapes."""
    result = model.eval(
        img_input,
        diameter=diameter,
        channels=channels,
        min_size=min_size,
    )
    # v2 returns (masks, flows, styles, diameters)
    # v3 returns (masks, flows, styles)
    masks = result[0]
    return masks


def segment_cellpose(
    image: np.ndarray,
    diameter: float | None = None,
    model_type: str = "nuclei",
    use_gpu: bool = False,
    min_size: int = 15,
) -> np.ndarray:
    """Segment nuclei using Cellpose (v2 and v3 compatible).

    Args:
        image: HxW or HxWxC uint8/float image.
        diameter: Expected cell/nucleus diameter in pixels.
                  None = Cellpose auto-estimates from the image.
        model_type: 'nuclei' for DAPI/GFP nuclei; 'cyto' or 'cyto2' for whole cells.
        use_gpu: Use CUDA acceleration if available.
        min_size: Minimum object area in pixels (Cellpose internal filter).

    Returns:
        Labeled int32 mask — 0 = background, 1…N = individual nuclei.

    Raises:
        ImportError: If the cellpose package is not installed.
    """
    try:
        from cellpose import models as _cp  # noqa: F401 — just to trigger ImportError
    except ImportError as exc:
        raise ImportError(
            "cellpose is not installed. Install it with:\n"
            "  pip install cellpose\n"
            "or with GPU support:\n"
            "  pip install 'cellpose[gui]'\n"
            "Documentation: https://cellpose.readthedocs.io"
        ) from exc

    model = _build_model(model_type, use_gpu)

    gray = to_grayscale(image)
    img_input = normalize_image(gray).astype(np.float32)

    # channels=None: grayscale input (v3 default); v2 equivalent is [0, 0]
    masks = _run_eval(model, img_input, diameter=diameter,
                      channels=None, min_size=min_size)

    return np.asarray(masks, dtype=np.int32)


def segment_cellpose_rgb(
    image: np.ndarray,
    diameter: float | None = None,
    cyto_channel: int = 1,
    nuclei_channel: int = 0,
    model_type: str = "cyto2",
    use_gpu: bool = False,
    min_size: int = 15,
) -> np.ndarray:
    """Segment cells using Cellpose with explicit RGB channel assignment.

    Useful when the cytoplasm is in one channel and the nucleus in another,
    e.g. DAPI (blue) + GFP (green) two-colour microscopy.

    Args:
        image: HxWxC uint8 image.
        cyto_channel: 1-indexed channel index for cytoplasm (0 = use grayscale).
        nuclei_channel: 1-indexed channel index for nuclei (0 = not provided).
        model_type: 'cyto', 'cyto2', or 'nuclei'.
        use_gpu: Use CUDA acceleration if available.
        min_size: Minimum object area in pixels.

    Returns:
        Labeled int32 mask.
    """
    try:
        from cellpose import models as _cp  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "cellpose is not installed. Run: pip install cellpose"
        ) from exc

    model = _build_model(model_type, use_gpu)
    masks = _run_eval(model, image, diameter=diameter,
                      channels=[cyto_channel, nuclei_channel], min_size=min_size)
    return np.asarray(masks, dtype=np.int32)
