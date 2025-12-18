import numpy as np
from skimage.filters import gaussian
from skimage.exposure import equalize_adapthist


def to_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.astype(np.float64)
    rgb = image[..., :3].astype(np.float64)
    return 0.2989 * rgb[..., 0] + 0.5870 * rgb[..., 1] + 0.1140 * rgb[..., 2]


def normalize_image(image: np.ndarray) -> np.ndarray:
    img = image.astype(np.float64)
    mn, mx = img.min(), img.max()
    if mx == mn:
        return np.zeros_like(img)
    return (img - mn) / (mx - mn)


def smooth_image(image: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    return gaussian(image, sigma=sigma)


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    norm = normalize_image(image)
    enhanced = equalize_adapthist(norm, clip_limit=0.03)
    return enhanced.astype(np.float64)
