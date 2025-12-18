"""ViT-based QC classifier for bioimage quality assessment.

Architecture:
  frozen ViT-B/16 backbone (ImageNet pretrained)
  → 768-d CLS token embedding
  → StandardScaler + LogisticRegression head

When torch/torchvision are unavailable, falls back to HOG features (~144-d)
extracted with scikit-image, so the module is usable in CPU-only environments.

Without a labeled QC dataset, pseudo-labels are generated automatically from the
rule-based QC module (blur + contrast + tiny-object checks) so the classifier is
self-bootstrapping from any image collection.

Typical usage:

    from src.vit_qc import ViTQCClassifier, train_vit_qc_from_dataset

    # Train once on the project dataset (rule-based pseudo-labels)
    clf = train_vit_qc_from_dataset("data/bbbc038_subset/images")
    clf.save("results/vit_qc_clf.pkl")

    # Inference on a new image
    result = clf.predict(image)
    # → {'label': 'pass', 'pass_prob': 0.94, 'fail_prob': 0.06,
    #    'backend': 'vit_b_16', 'trained': True}

Install:
    pip install torch torchvision     # for ViT backend (recommended)
    pip install scikit-learn          # required in both cases
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from src.preprocessing import to_grayscale, normalize_image
from src.qc import run_qc


# ── Feature extraction ────────────────────────────────────────────────────────

def _extract_vit_features(image: np.ndarray) -> np.ndarray:
    """Extract 768-d ViT-B/16 CLS-token features for one image.

    The model is loaded once and cached as a module-level attribute to avoid
    repeated disk I/O across many calls.
    """
    try:
        import torch
        import torchvision.models as tvm
        import torchvision.transforms.functional as TF
    except ImportError as exc:
        raise ImportError(
            "torch and torchvision are required for ViT features.\n"
            "Install: pip install torch torchvision"
        ) from exc

    # Build and cache model
    if not hasattr(_extract_vit_features, "_model"):
        weights = tvm.ViT_B_16_Weights.IMAGENET1K_V1
        model = tvm.vit_b_16(weights=weights)
        model.heads = torch.nn.Identity()  # strip classification head → 768-d output
        model.eval()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)
        _extract_vit_features._model = model
        _extract_vit_features._device = device
        _extract_vit_features._norm_mean = torch.tensor([0.485, 0.456, 0.406])
        _extract_vit_features._norm_std = torch.tensor([0.229, 0.224, 0.225])

    model = _extract_vit_features._model
    device = _extract_vit_features._device
    mean = _extract_vit_features._norm_mean
    std = _extract_vit_features._norm_std

    import torch

    gray = normalize_image(to_grayscale(image)).astype(np.float32)
    rgb = np.stack([gray, gray, gray], axis=0)  # CHW float32 [0, 1]
    tensor = torch.from_numpy(rgb)

    # Resize to 224×224 (ViT input size)
    tensor = TF.resize(tensor, [224, 224], antialias=True)

    # ImageNet normalisation
    tensor = (tensor - mean[:, None, None]) / std[:, None, None]

    with torch.no_grad():
        feat = model(tensor.unsqueeze(0).to(device))

    return feat.squeeze(0).cpu().numpy()  # shape (768,)


def _extract_hog_features(image: np.ndarray) -> np.ndarray:
    """Fallback feature extractor using HOG + image statistics (~152-d).

    Used when torch/torchvision are not installed.
    """
    from skimage.feature import hog
    from skimage.transform import resize
    from scipy.ndimage import laplace

    gray = normalize_image(to_grayscale(image))
    gray_r = resize(gray, (128, 128), anti_aliasing=True, preserve_range=True).astype(np.float32)

    hog_feat = hog(
        gray_r,
        orientations=8,
        pixels_per_cell=(16, 16),
        cells_per_block=(2, 2),
        feature_vector=True,
    )

    lap = np.abs(laplace(gray_r))
    stats = np.array([
        float(gray_r.mean()),
        float(gray_r.std()),
        float(np.percentile(gray_r, 10)),
        float(np.percentile(gray_r, 25)),
        float(np.percentile(gray_r, 75)),
        float(np.percentile(gray_r, 90)),
        float(lap.mean()),
        float(lap.std()),
        float(lap.var()),
    ], dtype=np.float32)

    return np.concatenate([hog_feat, stats])


def extract_features(image: np.ndarray) -> tuple[np.ndarray, str]:
    """Extract features for QC classification.

    Tries ViT-B/16 first; falls back to HOG if torch is unavailable.

    Returns:
        (feature_vector, backend_name)
    """
    try:
        return _extract_vit_features(image), "vit_b_16"
    except ImportError:
        return _extract_hog_features(image), "hog"


# ── Pseudo-label generation ───────────────────────────────────────────────────

def _blur_score_raw(image: np.ndarray) -> float:
    """Laplacian variance on the raw (unnormalized) image.

    `qc.blur_score()` normalises to [0, 1] first, which yields values far below
    the existing 20.0 threshold and flags every image as blurry.  This function
    operates on the original pixel scale so the threshold is meaningful.
    """
    from scipy.ndimage import convolve
    gray = to_grayscale(image)          # still in original scale (0–255 or float)
    lap  = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    return float(convolve(gray, lap).var())


def _rule_based_label(image: np.ndarray, mask: np.ndarray | None = None) -> int:
    """Return 1 (pass) or 0 (fail) for one image.

    Uses low-contrast and tiny-object flags from the QC module (reliable) plus
    a raw-pixel Laplacian variance for blur (avoids the normalisation issue in
    qc.blur_score that makes the 20.0 threshold always fire).
    """
    dummy = np.zeros(image.shape[:2], dtype=np.int32) if mask is None else mask
    qc = run_qc(image, dummy)
    is_blurry_raw = _blur_score_raw(image) < 20.0
    is_fail = is_blurry_raw or qc["is_low_contrast"] or qc["has_many_tiny_objects"]
    return 0 if is_fail else 1


# ── Rule-based fallback result ────────────────────────────────────────────────

def _rule_based_result(image: np.ndarray) -> dict:
    dummy = np.zeros(image.shape[:2], dtype=np.int32)
    qc = run_qc(image, dummy)
    is_fail = qc["is_blurry"] or qc["is_low_contrast"] or qc["has_many_tiny_objects"]
    return {
        "label": "fail" if is_fail else "pass",
        "pass_prob": 0.15 if is_fail else 0.85,
        "fail_prob": 0.85 if is_fail else 0.15,
        "backend": "rule_based",
        "trained": False,
    }


# ── Classifier ────────────────────────────────────────────────────────────────

class ViTQCClassifier:
    """Pass/fail image quality classifier using ViT (or HOG) features.

    The classifier is a sklearn Pipeline (StandardScaler + LogisticRegression)
    fitted on top of frozen ViT-B/16 embeddings.  When torch is not available
    it uses HOG features as a drop-in replacement.

    Training uses rule-based QC pseudo-labels by default, so no manually
    annotated QC dataset is required.

    Example::

        clf = ViTQCClassifier()
        clf.fit_from_images(images)        # auto pseudo-labels
        result = clf.predict(new_image)    # {'label': 'pass', 'pass_prob': ...}
        clf.save("vit_qc.pkl")

    """

    def __init__(self) -> None:
        self._clf = None
        self._backend: str | None = None
        self._threshold: float = 0.5

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(self, images: list[np.ndarray], labels: list[int]) -> "ViTQCClassifier":
        """Train on images with explicit binary labels (1 = pass, 0 = fail).

        Args:
            images: List of HxW or HxWxC numpy arrays.
            labels: Parallel list of int (0 or 1).

        Returns:
            self (for method chaining).
        """
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
        except ImportError as exc:
            raise ImportError(
                "scikit-learn is required for ViTQCClassifier.\n"
                "Install: pip install scikit-learn"
            ) from exc

        X, backend = [], None
        for img in images:
            feat, bk = extract_features(img)
            X.append(feat)
            backend = bk

        X_arr = np.array(X)
        y_arr = np.array(labels, dtype=int)

        self._clf = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced")),
        ])
        self._clf.fit(X_arr, y_arr)
        self._backend = backend
        return self

    def fit_from_images(
        self,
        images: list[np.ndarray],
        masks: list[np.ndarray] | None = None,
    ) -> "ViTQCClassifier":
        """Train using rule-based QC as pseudo-labels (self-supervised).

        Args:
            images: List of input images.
            masks: Optional parallel list of predicted segmentation masks.
                   Providing masks allows the tiny-object check to run.

        Returns:
            self (for method chaining).
        """
        labels = []
        for i, img in enumerate(images):
            mask = masks[i] if (masks and i < len(masks)) else None
            labels.append(_rule_based_label(img, mask))

        n_pass = sum(labels)
        n_fail = len(labels) - n_pass
        print(f"ViTQCClassifier: {len(labels)} images — {n_pass} pass, {n_fail} fail (pseudo-labels)")

        # Safety: if all images land in one class the logistic regression will fail.
        # Fall back to a relative split on raw blur score (median threshold).
        if n_pass == 0 or n_fail == 0:
            print("  WARNING: all pseudo-labels are the same class — "
                  "falling back to median-blur relative split.")
            blur_scores = [_blur_score_raw(img) for img in images]
            med = float(np.median(blur_scores))
            labels = [0 if b < med else 1 for b in blur_scores]
            n_pass = sum(labels)
            n_fail = len(labels) - n_pass
            print(f"  After split: {n_pass} pass, {n_fail} fail")

        return self.fit(images, labels)

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, image: np.ndarray) -> dict:
        """Predict QC label for a single image.

        Returns:
            dict with keys:
                label      — 'pass' or 'fail'
                pass_prob  — probability that the image passes QC
                fail_prob  — probability that the image fails QC
                backend    — 'vit_b_16' or 'hog'
                trained    — True (always when called on a fitted classifier)
        """
        if self._clf is None:
            return _rule_based_result(image)

        feat, _ = extract_features(image)
        proba = self._clf.predict_proba([feat])[0]
        classes = list(self._clf.named_steps["lr"].classes_)

        # classes may be [0, 1] or [1] depending on data
        if 1 in classes:
            pass_prob = float(proba[classes.index(1)])
        else:
            pass_prob = float(proba[-1])

        return {
            "label": "pass" if pass_prob >= self._threshold else "fail",
            "pass_prob": pass_prob,
            "fail_prob": 1.0 - pass_prob,
            "backend": self._backend or "unknown",
            "trained": True,
        }

    def predict_batch(self, images: list[np.ndarray]) -> list[dict]:
        """Predict QC labels for multiple images."""
        return [self.predict(img) for img in images]

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Save fitted classifier to a pickle file."""
        with open(path, "wb") as fh:
            pickle.dump(
                {
                    "clf": self._clf,
                    "backend": self._backend,
                    "threshold": self._threshold,
                },
                fh,
            )

    def load(self, path: str | Path) -> "ViTQCClassifier":
        """Load a previously saved classifier from disk."""
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        self._clf = d["clf"]
        self._backend = d["backend"]
        self._threshold = d.get("threshold", 0.5)
        return self


# ── Convenience functions ─────────────────────────────────────────────────────

def predict_vit_qc(
    image: np.ndarray,
    classifier: ViTQCClassifier | None = None,
) -> dict:
    """Predict QC for one image.

    Args:
        image: Input microscopy image.
        classifier: A fitted ViTQCClassifier. If None, uses rule-based fallback.

    Returns:
        dict with label, pass_prob, fail_prob, backend, trained.
    """
    if classifier is not None:
        return classifier.predict(image)
    return _rule_based_result(image)


def train_vit_qc_from_dataset(
    image_dir: str | Path,
    mask_dir: str | Path | None = None,
    out_model: str | Path | None = None,
    limit: int | None = None,
) -> ViTQCClassifier:
    """Train a ViTQCClassifier on a directory of images.

    Pseudo-labels are generated from the rule-based QC module so no manual
    annotation is required.

    Args:
        image_dir: Directory containing PNG/TIF images.
        mask_dir:  Optional directory with segmentation masks (improves pseudo-labels).
        out_model: Optional path to save the trained classifier (.pkl).
        limit:     Cap the number of training images (useful for quick tests).

    Returns:
        Fitted ViTQCClassifier.
    """
    from src.load_data import list_image_files, load_image, load_mask

    image_dir = Path(image_dir)
    img_files = sorted(list_image_files(image_dir))
    if limit:
        img_files = img_files[:limit]

    images: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    has_masks = False

    for img_path in img_files:
        img = load_image(img_path)
        images.append(img)

        if mask_dir:
            mp = Path(mask_dir) / img_path.name
            if mp.exists():
                masks.append(load_mask(mp))
                has_masks = True
            else:
                masks.append(None)

    clf = ViTQCClassifier()
    clf.fit_from_images(images, masks if has_masks else None)

    if out_model:
        clf.save(out_model)
        print(f"Saved ViT QC classifier → {out_model}")

    return clf
