import numpy as np
from scipy.ndimage import distance_transform_edt, label as nd_label
from skimage.filters import threshold_otsu, gaussian
from skimage.feature import peak_local_max
from skimage.morphology import remove_small_objects
from skimage.segmentation import watershed
from skimage.measure import label, regionprops

from src.preprocessing import to_grayscale, normalize_image, enhance_contrast


def segment_watershed(
    image: np.ndarray,
    sigma: float = 1.0,
    min_size: int = 30,
    min_distance: int = 5,
    use_contrast_enhancement: bool = False,
) -> np.ndarray:
    gray = to_grayscale(image)
    norm = normalize_image(gray)

    if use_contrast_enhancement:
        norm = enhance_contrast(norm)

    smoothed = gaussian(norm, sigma=sigma)

    thresh = threshold_otsu(smoothed)
    binary = smoothed > thresh

    binary = remove_small_objects(binary, max_size=min_size)

    if not binary.any():
        return np.zeros(image.shape[:2], dtype=np.int32)

    distance = distance_transform_edt(binary)

    coords = peak_local_max(distance, min_distance=min_distance, labels=binary)
    markers = np.zeros(distance.shape, dtype=np.int32)
    markers[tuple(coords.T)] = np.arange(1, len(coords) + 1)

    labeled = watershed(-distance, markers, mask=binary)

    labeled = remove_small_objects(labeled > 0, max_size=min_size).astype(np.int32)
    labeled, _ = nd_label(labeled)

    return labeled.astype(np.int32)
