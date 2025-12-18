"""
Bioimage QC Agent — Interactive Streamlit demo (cloud-safe, watershed only).
Deploy on Streamlit Cloud: push to GitHub, point at this file.
"""
import io
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.segment_watershed import segment_watershed
from src.qc import run_qc
from src.rule_agent import analyze_segmentation
from src.preprocessing import normalize_image, to_grayscale
from src.visualize import create_overlay

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bioimage QC Agent",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .metric-card {
    background: #1A1D27;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 8px;
    border-left: 4px solid #00C9A7;
  }
  .metric-label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.08em; }
  .metric-value { font-size: 1.6rem; font-weight: 700; color: #FAFAFA; margin: 2px 0; }
  .badge-pass   { background:#00C9A7; color:#0E1117; border-radius:6px; padding:3px 12px; font-weight:700; font-size:0.9rem; }
  .badge-fail   { background:#FF6B6B; color:#0E1117; border-radius:6px; padding:3px 12px; font-weight:700; font-size:0.9rem; }
  .badge-warn   { background:#FFD166; color:#0E1117; border-radius:6px; padding:3px 12px; font-weight:700; font-size:0.9rem; }
  .agent-box    { background:#1A1D27; border-radius:10px; padding:20px 24px; border:1px solid #2a2d3a; }
  .section-title { font-size:1.1rem; font-weight:600; color:#00C9A7; margin-bottom:12px; }
  div[data-testid="stImage"] img { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────
SAMPLES = {
    "Dense nuclei (fluorescence)": {
        "image": Path("docs/samples/dense_nuclei.png"),
        "mask":  Path("docs/samples/dense_nuclei_mask.png"),
        "desc":  "67 tightly packed nuclei — Dice ~0.95 with default params",
    },
    "Sparse nuclei (fluorescence)": {
        "image": Path("docs/samples/sparse_nuclei.png"),
        "mask":  Path("docs/samples/sparse_nuclei_mask.png"),
        "desc":  "12 well-separated nuclei — Dice ~0.94 with default params",
    },
    "Challenging brightfield": {
        "image": Path("docs/samples/challenging_brightfield.png"),
        "mask":  None,
        "desc":  "Dense brightfield sample — agent detects failure & recommends a fix",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_image_array(source) -> np.ndarray:
    """Load from a Path or UploadedFile into an HxW or HxWxC uint8 array."""
    if isinstance(source, Path):
        return np.array(Image.open(source))
    return np.array(Image.open(source))


def load_mask_array(source) -> np.ndarray | None:
    if source is None:
        return None
    if isinstance(source, Path) and not source.exists():
        return None
    return np.array(Image.open(source))


def colored_mask(labeled: np.ndarray) -> np.ndarray:
    """Return an RGB image with each nucleus in a distinct colour."""
    n = int(labeled.max())
    if n == 0:
        return np.zeros((*labeled.shape, 3), dtype=np.uint8)
    cmap = matplotlib.colormaps["nipy_spectral"]
    rgb = np.zeros((*labeled.shape, 3), dtype=np.uint8)
    for i in range(1, n + 1):
        r, g, b, _ = cmap(i / (n + 1))
        rgb[labeled == i] = [int(r * 255), int(g * 255), int(b * 255)]
    return rgb


def size_histogram(labeled: np.ndarray) -> bytes:
    """Return a PNG bytes of the nucleus area histogram."""
    areas = []
    n = int(labeled.max())
    for i in range(1, n + 1):
        areas.append(int((labeled == i).sum()))
    fig, ax = plt.subplots(figsize=(3.8, 2.2), facecolor="#1A1D27")
    ax.set_facecolor("#1A1D27")
    if areas:
        ax.hist(areas, bins=min(20, max(5, n // 3 + 1)),
                color="#00C9A7", edgecolor="#0E1117", linewidth=0.5)
    ax.set_xlabel("Nucleus area (px²)", color="#888", fontsize=8)
    ax.set_ylabel("Count", color="#888", fontsize=8)
    ax.tick_params(colors="#888", labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor("#2a2d3a")
    ax.set_title("Nucleus size distribution", color="#FAFAFA", fontsize=9, pad=6)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor="#1A1D27")
    plt.close(fig)
    return buf.getvalue()


def dice_score(gt: np.ndarray, pred: np.ndarray) -> float:
    gt_bin  = (gt  > 0).astype(bool)
    pred_bin = (pred > 0).astype(bool)
    intersection = (gt_bin & pred_bin).sum()
    denom = gt_bin.sum() + pred_bin.sum()
    return float(2 * intersection / denom) if denom > 0 else float("nan")


def iou_score(gt: np.ndarray, pred: np.ndarray) -> float:
    gt_bin   = (gt   > 0).astype(bool)
    pred_bin = (pred > 0).astype(bool)
    inter = (gt_bin & pred_bin).sum()
    union = (gt_bin | pred_bin).sum()
    return float(inter / union) if union > 0 else float("nan")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔬 Bioimage QC Agent")
    st.caption("Watershed segmentation · rule-based QC · agentic loop")
    st.divider()

    # ── Image source ──────────────────────────────────────────────────────────
    st.markdown("### Image source")
    source_tab = st.radio("", ["Sample gallery", "Upload your own"],
                          label_visibility="collapsed")

    image_raw   = None
    gt_mask_raw = None
    source_name = ""

    if source_tab == "Sample gallery":
        chosen = st.selectbox("Pick a sample", list(SAMPLES.keys()))
        meta = SAMPLES[chosen]
        st.caption(f"_{meta['desc']}_")
        image_raw   = load_image_array(meta["image"])
        gt_mask_raw = load_mask_array(meta["mask"])
        source_name = chosen
    else:
        up_img = st.file_uploader("Upload image", type=["png","jpg","jpeg","tif","tiff"])
        up_gt  = st.file_uploader("Ground truth mask (optional)", type=["png","tif","tiff"])
        if up_img:
            image_raw   = load_image_array(up_img)
            gt_mask_raw = load_mask_array(up_gt) if up_gt else None
            source_name = up_img.name

    st.divider()

    # ── Watershed parameters ──────────────────────────────────────────────────
    st.markdown("### Watershed parameters")
    sigma        = st.slider("Smoothing (σ)",          0.5, 5.0, 1.0, 0.5,
                             help="Gaussian blur before thresholding. Higher = smoother, fewer objects.")
    min_size     = st.slider("Min object size (px)",   10,  300, 30,  10,
                             help="Objects smaller than this are discarded.")
    min_distance = st.slider("Peak min distance (px)", 1,   30,  5,   1,
                             help="Minimum distance between detected nuclei centres.")
    use_ce       = st.checkbox("Contrast enhancement", value=False,
                               help="CLAHE pre-processing — helps on low-contrast images.")

    st.divider()
    auto_rerun = st.checkbox("Auto-rerun with agent fix", value=True,
                             help="If the agent detects a failure, it will re-run with corrected parameters.")

    st.divider()
    run_btn = st.button("▶  Run segmentation", type="primary", use_container_width=True)

# ── Landing ───────────────────────────────────────────────────────────────────
if image_raw is None:
    st.markdown("## 🔬 Bioimage QC Agent")
    st.markdown(
        "Upload a fluorescence or brightfield microscopy image, tune the watershed "
        "parameters, and let the agent inspect the segmentation quality — automatically "
        "detecting over/under-segmentation, blur, and low contrast, then recommending "
        "and applying a fix."
    )
    c1, c2, c3 = st.columns(3)
    for col, (name, meta) in zip([c1, c2, c3], SAMPLES.items()):
        with col:
            st.image(str(meta["image"]), caption=name, use_container_width=True)
    st.info("Select a sample from the sidebar or upload your own image to get started.")
    st.stop()

if not run_btn:
    st.info("Configure parameters in the sidebar, then click **▶ Run segmentation**.")
    # show the raw image while waiting
    gray = normalize_image(to_grayscale(image_raw))
    st.image((gray * 255).astype(np.uint8), caption=source_name, width=400)
    st.stop()

# ── Run segmentation ──────────────────────────────────────────────────────────
ws_params = dict(sigma=sigma, min_size=min_size, min_distance=min_distance,
                 use_contrast_enhancement=use_ce)

with st.spinner("Segmenting…"):
    pred_mask = segment_watershed(image_raw, **ws_params)

# ── QC + agent ────────────────────────────────────────────────────────────────
gt_count = int(gt_mask_raw.max()) if gt_mask_raw is not None else 0
metrics = {
    "dice": dice_score(gt_mask_raw, pred_mask) if gt_mask_raw is not None else float("nan"),
    "iou":  iou_score(gt_mask_raw, pred_mask)  if gt_mask_raw is not None else float("nan"),
    "num_objects_gt":   gt_count,
    "num_objects_pred": int(pred_mask.max()),
    "object_count_error": abs(gt_count - int(pred_mask.max())) if gt_mask_raw is not None else 0,
}
qc = run_qc(image_raw, pred_mask)
decision = analyze_segmentation(metrics, qc, ws_params)

# ── Auto-rerun ────────────────────────────────────────────────────────────────
rerun_mask    = None
rerun_metrics = None
rerun_qc      = None

if auto_rerun and decision["should_rerun"]:
    with st.spinner("Agent re-running with corrected parameters…"):
        rerun_mask = segment_watershed(image_raw, **decision["new_params"])
        rerun_metrics = {
            "dice": dice_score(gt_mask_raw, rerun_mask) if gt_mask_raw is not None else float("nan"),
            "iou":  iou_score(gt_mask_raw, rerun_mask)  if gt_mask_raw is not None else float("nan"),
            "num_objects_gt":   gt_count,
            "num_objects_pred": int(rerun_mask.max()),
            "object_count_error": abs(gt_count - int(rerun_mask.max())) if gt_mask_raw is not None else 0,
        }
        rerun_qc = run_qc(image_raw, rerun_mask)
        kept = (
            rerun_metrics["dice"] >= metrics["dice"]
            if gt_mask_raw is not None
            else (not rerun_qc["has_many_tiny_objects"] and rerun_mask.max() > 0)
        )
        best_mask    = rerun_mask if kept else pred_mask
        best_metrics = rerun_metrics if kept else metrics
else:
    best_mask    = pred_mask
    best_metrics = metrics

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(f"## 🔬 {source_name}")

# ── Visualisation row ─────────────────────────────────────────────────────────
v1, v2, v3, v4 = st.columns(4)

gray_u8 = (normalize_image(to_grayscale(image_raw)) * 255).astype(np.uint8)
overlay  = create_overlay(image_raw, best_mask, gt_mask_raw)
col_seg  = colored_mask(best_mask)

with v1:
    st.markdown('<p class="section-title">Raw image</p>', unsafe_allow_html=True)
    st.image(gray_u8, use_container_width=True)

with v2:
    st.markdown('<p class="section-title">Segmentation</p>', unsafe_allow_html=True)
    st.image(col_seg, use_container_width=True)

with v3:
    label = "Overlay (red=pred, green=GT)" if gt_mask_raw is not None else "Overlay (red=pred)"
    st.markdown(f'<p class="section-title">{label}</p>', unsafe_allow_html=True)
    st.image(overlay, use_container_width=True)

with v4:
    st.markdown('<p class="section-title">Nucleus size histogram</p>', unsafe_allow_html=True)
    st.image(size_histogram(best_mask), use_container_width=True)

st.divider()

# ── Metrics row ───────────────────────────────────────────────────────────────
m1, m2, m3, m4, m5 = st.columns(5)

def mcard(col, label, value, delta=None):
    with col:
        delta_html = f'<div style="font-size:0.75rem;color:#888">{delta}</div>' if delta else ""
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-label">{label}</div>'
            f'<div class="metric-value">{value}</div>'
            f'{delta_html}</div>',
            unsafe_allow_html=True,
        )

has_gt = gt_mask_raw is not None
mcard(m1, "Predicted objects", best_metrics["num_objects_pred"])
mcard(m2, "Ground truth objects", best_metrics["num_objects_gt"] if has_gt else "—")
mcard(m3, "Dice score",  f"{best_metrics['dice']:.3f}" if has_gt else "—", "higher = better")
mcard(m4, "IoU",         f"{best_metrics['iou']:.3f}"  if has_gt else "—", "higher = better")
mcard(m5, "Count error", best_metrics["object_count_error"] if has_gt else "—")

st.divider()

# ── QC + Agent side by side ───────────────────────────────────────────────────
qc_col, agent_col = st.columns([1, 1])

with qc_col:
    st.markdown('<p class="section-title">Rule-based QC</p>', unsafe_allow_html=True)
    q1, q2, q3 = st.columns(3)
    with q1:
        st.metric("Blur score",    f"{qc['blur_score']:.1f}",    help="Laplacian variance. <20 = blurry.")
    with q2:
        st.metric("Contrast",      f"{qc['contrast_score']:.3f}", help="Pixel std dev. <0.05 = low contrast.")
    with q3:
        st.metric("Tiny fraction", f"{qc['tiny_object_fraction']:.2f}", help=">0.25 = many noise objects.")

    flags = []
    if qc["is_blurry"]:          flags.append("🌫 Blurry image")
    if qc["is_low_contrast"]:    flags.append("🔅 Low contrast")
    if qc["has_many_tiny_objects"]: flags.append("🔸 Many tiny objects")

    if flags:
        for f in flags:
            st.warning(f)
    else:
        st.success("No QC flags — image quality looks good.")

with agent_col:
    st.markdown('<p class="section-title">Agent decision</p>', unsafe_allow_html=True)

    badge_cls = "badge-pass" if decision["status"] == "pass" else (
        "badge-warn" if decision["should_rerun"] else "badge-fail")
    badge_txt = decision["status"].upper()

    st.markdown(
        f'<div class="agent-box">'
        f'<span class="{badge_cls}">{badge_txt}</span>'
        f'<p style="margin-top:12px;margin-bottom:4px"><b>Failure mode:</b> {decision["failure_mode"]}</p>'
        f'<p style="margin-bottom:4px"><b>Action:</b> {decision["recommended_action"]}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if rerun_mask is not None:
        st.divider()
        st.markdown("**Agent rerun comparison**")
        r1, r2 = st.columns(2)
        with r1:
            st.markdown("Initial")
            st.write(f"Objects: **{metrics['num_objects_pred']}**")
            if has_gt:
                st.write(f"Dice: **{metrics['dice']:.3f}**")
        with r2:
            st.markdown("After fix")
            st.write(f"Objects: **{rerun_metrics['num_objects_pred']}**")
            if has_gt:
                delta = rerun_metrics["dice"] - metrics["dice"]
                arrow = "▲" if delta >= 0 else "▼"
                color = "#00C9A7" if delta >= 0 else "#FF6B6B"
                st.markdown(f"Dice: **{rerun_metrics['dice']:.3f}** "
                            f"<span style='color:{color}'>{arrow} {abs(delta):.3f}</span>",
                            unsafe_allow_html=True)

st.divider()

# ── Downloads ─────────────────────────────────────────────────────────────────
st.markdown('<p class="section-title">Download results</p>', unsafe_allow_html=True)
dl1, dl2, dl3 = st.columns(3)

with dl1:
    ov_bytes = io.BytesIO()
    Image.fromarray(overlay).save(ov_bytes, format="PNG")
    st.download_button("⬇ Overlay PNG", ov_bytes.getvalue(),
                       "overlay.png", "image/png", use_container_width=True)

with dl2:
    mask_img = Image.fromarray(best_mask.astype(np.uint16))
    mask_bytes = io.BytesIO()
    mask_img.save(mask_bytes, format="PNG")
    st.download_button("⬇ Segmentation mask", mask_bytes.getvalue(),
                       "mask.png", "image/png", use_container_width=True)

with dl3:
    import pandas as pd
    row = {
        "source":          source_name,
        "num_objects_pred": best_metrics["num_objects_pred"],
        "num_objects_gt":   best_metrics["num_objects_gt"],
        "dice":             best_metrics["dice"],
        "iou":              best_metrics["iou"],
        "blur_score":       qc["blur_score"],
        "contrast_score":   qc["contrast_score"],
        "tiny_fraction":    qc["tiny_object_fraction"],
        "agent_status":     decision["status"],
        "agent_action":     decision["recommended_action"],
        "sigma":            sigma,
        "min_size":         min_size,
        "min_distance":     min_distance,
    }
    csv_buf = io.StringIO()
    pd.DataFrame([row]).to_csv(csv_buf, index=False)
    st.download_button("⬇ Metrics CSV", csv_buf.getvalue(),
                       "metrics.csv", "text/csv", use_container_width=True)
