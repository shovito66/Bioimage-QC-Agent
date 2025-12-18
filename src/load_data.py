from pathlib import Path

import numpy as np
from PIL import Image

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def load_image(path: str | Path) -> np.ndarray:
    path = Path(path)
    img = Image.open(path)
    arr = np.array(img)
    if arr.ndim == 2:
        return arr
    if arr.shape[2] == 4:
        img = img.convert("RGB")
        arr = np.array(img)
    if arr.ndim == 3:
        return arr
    return arr


def load_mask(path: str | Path) -> np.ndarray:
    arr = np.array(Image.open(path))
    if arr.ndim == 3:
        arr = arr[..., 0]
    return arr.astype(np.int32)


def list_image_files(image_dir: str | Path) -> list[Path]:
    image_dir = Path(image_dir)
    files = sorted(
        p for p in image_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    return files


def match_image_mask_files(
    image_dir: str | Path, mask_dir: str | Path | None
) -> list[tuple[Path, Path | None]]:
    image_dir = Path(image_dir)
    image_files = list_image_files(image_dir)

    if mask_dir is None:
        return [(img, None) for img in image_files]

    mask_dir = Path(mask_dir)
    if not mask_dir.exists():
        return [(img, None) for img in image_files]

    mask_stems = {p.stem: p for p in mask_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS}

    pairs = []
    for img_path in image_files:
        mask_path = mask_stems.get(img_path.stem)
        pairs.append((img_path, mask_path))
    return pairs
