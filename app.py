import sys
from pathlib import Path

import numpy as np
import streamlit as st
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from src.load_data import load_image, load_mask, list_image_files
from src.segment_watershed import segment_watershed
from src.evaluate import evaluate_segmentation
from src.qc import run_qc
from src.rule_agent import analyze_segmentation
from src.llm_agent import generate_agent_explanation
from src.visualize import create_overlay
from src.preprocessing import normalize_image, to_grayscale
from src.vit_qc import predict_vit_qc, ViTQCClassifier

st.set_page_config(page_title="Bioimage QC Agent", layout="wide")
st.title("Bioimage QC Agent")
st.caption(
    "Nuclei segmentation quality control — watershed · Cellpose · SAM · ViT QC"
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Input")

    data_dir = Path("data/bbbc038_subset/images")
    sample_files = (
        list(data_dir.glob("*.png")) + list(data_dir.glob("*.tif"))
        if data_dir.exists()
        else []
    )
    sample_names = [f.name for f in sample_files]

    uploaded_image = st.file_uploader(
        "Upload image", type=["png", "jpg", "jpeg", "tif", "tiff"]
    )
    selected_sample = None
    if sample_names:
        selected_sample = st.selectbox(
            "Or choose a sample image", ["-- none --"] + sample_names
        )

    uploaded_mask = st.file_uploader(
        "Upload ground truth mask (optional)", type=["png", "tif"]
    )

    st.divider()

    # ── Segmentation method ───────────────────────────────────────────────────
    st.header("Segmentation Method")
    method = st.selectbox(
        "Method",
        ["watershed", "cellpose", "sam", "microsam"],
        index=0,
        help=(
            "watershed — classical (no install needed)\n"
            "cellpose  — deep learning (pip install cellpose)\n"
            "sam       — Segment Anything Model (pip install segment-anything + checkpoint)\n"
            "microsam  — SAM fine-tuned for microscopy (pip install micro-sam)"
        ),
    )

    st.divider()
    st.header("Watershed Parameters")
    sigma = st.slider("Sigma (smoothing)", 0.5, 5.0, 1.0, 0.5)
    min_size = st.slider("Min object size (px)", 10, 200, 30, 10)
    min_distance = st.slider("Min distance between peaks", 1, 20, 5, 1)
    auto_rerun = st.checkbox("Auto-rerun with QC Agent (watershed only)", value=False)

    # Method-specific params
    cellpose_diameter = None
    sam_checkpoint = None
    sam_model_type = "vit_b"

    if method == "cellpose":
        st.divider()
        st.header("Cellpose Parameters")
        diam_str = st.text_input("Diameter (px) — leave blank for auto", value="")
        cellpose_diameter = float(diam_str) if diam_str.strip() else None

    if method in ("sam", "microsam"):
        st.divider()
        st.header("SAM Parameters")
        sam_checkpoint = st.text_input(
            "Checkpoint path (.pth)",
            value="",
            help="Download: https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
        ) or None
        sam_model_type = st.selectbox(
            "Model type",
            ["vit_b", "vit_l", "vit_h", "vit_b_lm", "vit_l_lm"],
            index=0,
        )

    st.divider()
    st.header("ViT QC Classifier")
    use_vit_qc = st.checkbox("Enable ViT QC prediction", value=False,
                              help="Requires scikit-learn (+ torch for ViT features)")
    vit_qc_model_path = None
    if use_vit_qc:
        vit_qc_model_path = st.text_input(
            "Saved classifier path (optional .pkl)", value=""
        ) or None

    st.divider()
    st.header("LLM Provider")
    llm_provider = st.selectbox(
        "Explanation provider",
        ["template", "ollama", "gemini"],
        index=0,
    )
    gemini_key = None
    if llm_provider == "gemini":
        gemini_key = st.text_input("Gemini API key", type="password")

    run_btn = st.button("Run Segmentation", type="primary")

# ── Load image ────────────────────────────────────────────────────────────────
image = None
gt_mask = None

if run_btn:
    if uploaded_image is not None:
        image = np.array(Image.open(uploaded_image))
    elif selected_sample and selected_sample != "-- none --":
        img_path = data_dir / selected_sample
        image = load_image(img_path)
        mask_path = Path("data/bbbc038_subset/masks") / selected_sample
        if mask_path.exists():
            gt_mask = load_mask(mask_path)
    else:
        st.warning("Please upload an image or select a sample.")

    if uploaded_mask is not None:
        gt_mask = np.array(Image.open(uploaded_mask))

if image is None and not run_btn:
    st.info("Configure parameters in the sidebar, then click **Run Segmentation**.")
    st.stop()

if image is None:
    st.stop()

# ── Segmentation ──────────────────────────────────────────────────────────────
ws_params = {
    "sigma": sigma,
    "min_size": min_size,
    "min_distance": min_distance,
    "use_contrast_enhancement": False,
}

pred_mask = None
seg_error = None

with st.spinner(f"Running segmentation [{method}]…"):
    if method == "watershed":
        pred_mask = segment_watershed(image, **ws_params)

    elif method == "cellpose":
        try:
            from src.segment_cellpose import segment_cellpose
            pred_mask = segment_cellpose(image, diameter=cellpose_diameter,
                                         min_size=min_size)
        except ImportError as exc:
            seg_error = str(exc)
            pred_mask = segment_watershed(image, **ws_params)

    elif method == "sam":
        try:
            from src.segment_sam import segment_sam
            if not sam_checkpoint:
                seg_error = (
                    "SAM requires a checkpoint file. "
                    "Download sam_vit_b_01ec64.pth and enter the path above."
                )
                pred_mask = segment_watershed(image, **ws_params)
            else:
                pred_mask = segment_sam(image, checkpoint_path=sam_checkpoint,
                                        model_type=sam_model_type)
        except ImportError as exc:
            seg_error = str(exc)
            pred_mask = segment_watershed(image, **ws_params)

    elif method == "microsam":
        try:
            from src.segment_sam import segment_microsam
            pred_mask = segment_microsam(image, model_type=sam_model_type,
                                          checkpoint_path=sam_checkpoint or None)
        except ImportError as exc:
            seg_error = str(exc)
            pred_mask = segment_watershed(image, **ws_params)

if seg_error:
    st.warning(f"**{method} not available** — fell back to watershed.\n\n{seg_error}")

# ── Metrics & QC ──────────────────────────────────────────────────────────────
metrics = (
    evaluate_segmentation(gt_mask, pred_mask)
    if gt_mask is not None
    else {
        "dice": float("nan"),
        "iou": float("nan"),
        "num_objects_gt": 0,
        "num_objects_pred": int(pred_mask.max()),
        "object_count_error": 0,
    }
)
metrics["num_objects_pred"] = int(pred_mask.max())

qc = run_qc(image, pred_mask)
decision = analyze_segmentation(metrics, qc, ws_params)
explanation = generate_agent_explanation(
    metrics, qc, decision, provider=llm_provider, api_key=gemini_key
)

# ── ViT QC ────────────────────────────────────────────────────────────────────
vit_result = None
if use_vit_qc:
    with st.spinner("Running ViT QC classifier…"):
        try:
            vit_clf = None
            if vit_qc_model_path and Path(vit_qc_model_path).exists():
                vit_clf = ViTQCClassifier().load(vit_qc_model_path)
            vit_result = predict_vit_qc(image, vit_clf)
        except Exception as exc:
            st.warning(f"ViT QC failed: {exc}. Using rule-based fallback.")
            vit_result = None

# ── Auto-rerun (watershed only) ───────────────────────────────────────────────
best_mask = pred_mask
rerun_metrics = None
rerun_done = False

if auto_rerun and decision["should_rerun"] and method == "watershed":
    with st.spinner("Agent re-running with adjusted parameters…"):
        new_params = decision["new_params"]
        rerun_mask = segment_watershed(image, **new_params)
        rerun_metrics = (
            evaluate_segmentation(gt_mask, rerun_mask)
            if gt_mask is not None
            else {
                "dice": float("nan"),
                "iou": float("nan"),
                "num_objects_gt": 0,
                "num_objects_pred": int(rerun_mask.max()),
                "object_count_error": 0,
            }
        )
        rerun_metrics["num_objects_pred"] = int(rerun_mask.max())
        rerun_qc = run_qc(image, rerun_mask)

        if gt_mask is not None:
            kept_rerun = rerun_metrics["dice"] >= metrics["dice"]
        else:
            kept_rerun = (
                not rerun_qc["has_many_tiny_objects"]
                and rerun_qc["num_objects_pred"] > 0
            )

        if kept_rerun:
            best_mask = rerun_mask
        rerun_done = True

# ── Display ───────────────────────────────────────────────────────────────────
gray_display = normalize_image(to_grayscale(image))
overlay = create_overlay(image, best_mask, gt_mask)

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.subheader("Original Image")
    st.image(gray_display, clamp=True)
with col2:
    if gt_mask is not None:
        st.subheader("Ground Truth Mask")
        gt_disp = (gt_mask > 0).astype(np.uint8) * 255
        st.image(gt_disp)
    else:
        st.subheader("Ground Truth")
        st.caption("Not provided.")
with col3:
    st.subheader("Predicted Mask")
    pred_disp = (best_mask > 0).astype(np.uint8) * 255
    st.image(pred_disp)
with col4:
    st.subheader("Overlay")
    st.image(overlay)

st.divider()

# ── Metrics + QC + ViT QC columns ────────────────────────────────────────────
col_m, col_q, col_v = st.columns(3)

with col_m:
    st.subheader("Metrics")
    if gt_mask is not None:
        st.metric("Dice", f"{metrics['dice']:.3f}")
        st.metric("IoU", f"{metrics['iou']:.3f}")
        st.metric("Object count error", metrics["object_count_error"])
    st.metric("Predicted objects", metrics["num_objects_pred"])
    if gt_mask is not None:
        st.metric("Ground truth objects", metrics["num_objects_gt"])

with col_q:
    st.subheader("Rule-based QC")
    st.metric("Blur score", f"{qc['blur_score']:.2f}")
    st.metric("Contrast score", f"{qc['contrast_score']:.3f}")
    st.metric("Tiny object fraction", f"{qc['tiny_object_fraction']:.2f}")
    flags = []
    if qc["is_blurry"]:
        flags.append("Blurry")
    if qc["is_low_contrast"]:
        flags.append("Low contrast")
    if qc["has_many_tiny_objects"]:
        flags.append("Many tiny objects")
    if flags:
        st.warning("Flags: " + ", ".join(flags))
    else:
        st.success("No QC flags.")

with col_v:
    st.subheader("ViT QC Classifier")
    if vit_result is not None:
        label_color = "green" if vit_result["label"] == "pass" else "red"
        st.markdown(
            f"**Label:** :{label_color}[{vit_result['label'].upper()}]"
        )
        st.metric("Pass probability", f"{vit_result['pass_prob']:.2f}")
        st.metric("Fail probability", f"{vit_result['fail_prob']:.2f}")
        st.caption(
            f"Backend: **{vit_result['backend']}**  |  "
            f"Trained: {'yes' if vit_result['trained'] else 'no (rule-based fallback)'}"
        )
    elif use_vit_qc:
        st.caption("ViT QC result unavailable.")
    else:
        st.caption("Enable ViT QC in the sidebar to see predictions here.")

st.divider()
st.subheader("Agent Decision")
status_color = "green" if decision["status"] == "pass" else "orange"
st.markdown(f"**Status:** :{status_color}[{decision['status']}]")
st.markdown(f"**Failure mode:** {decision['failure_mode']}")
st.markdown(f"**Recommended action:** {decision['recommended_action']}")

st.subheader("Agent Explanation")
st.info(explanation)

if rerun_done:
    st.divider()
    st.subheader("Rerun Comparison")
    r_col1, r_col2 = st.columns(2)
    with r_col1:
        st.markdown("**Initial**")
        if gt_mask is not None:
            st.write(f"Dice: {metrics['dice']:.3f}  |  IoU: {metrics['iou']:.3f}")
        st.write(f"Predicted objects: {metrics['num_objects_pred']}")
    with r_col2:
        st.markdown("**After rerun**")
        if gt_mask is not None and rerun_metrics:
            st.write(
                f"Dice: {rerun_metrics['dice']:.3f}  |  IoU: {rerun_metrics['iou']:.3f}"
            )
        if rerun_metrics:
            st.write(f"Predicted objects: {rerun_metrics['num_objects_pred']}")
    st.caption(
        "Best result shown above."
        if best_mask is not pred_mask
        else "Initial result was retained."
    )

st.divider()
import io, pandas as pd

row = {**metrics, **{f"qc_{k}": v for k, v in qc.items()}}
if vit_result:
    row.update({f"vit_{k}": v for k, v in vit_result.items()})

metrics_df = pd.DataFrame([row])
csv_buf = io.StringIO()
metrics_df.to_csv(csv_buf, index=False)
st.download_button("Download metrics CSV", csv_buf.getvalue(), "metrics.csv", "text/csv")

vit_section = ""
if vit_result:
    vit_section = (
        f"\n\n### ViT QC Classifier\n"
        f"- Label: {vit_result['label'].upper()}\n"
        f"- Pass probability: {vit_result['pass_prob']:.2f}\n"
        f"- Backend: {vit_result['backend']}\n"
    )

report_lines = [
    "# Bioimage QC Agent Report\n",
    f"## {decision['status'].upper()}",
    f"- Failure mode: {decision['failure_mode']}",
    f"- Recommended action: {decision['recommended_action']}",
    vit_section,
    "### Explanation",
    explanation,
]
st.download_button(
    "Download agent report",
    "\n".join(report_lines),
    "agent_report.md",
    "text/markdown",
)
