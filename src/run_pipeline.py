import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.load_data import match_image_mask_files, load_image, load_mask
from src.segment_watershed import segment_watershed
from src.evaluate import evaluate_segmentation
from src.qc import run_qc
from src.rule_agent import analyze_segmentation
from src.llm_agent import generate_agent_explanation
from src.visualize import save_overlay
from src.vit_qc import predict_vit_qc


def _default_metrics() -> dict:
    return {
        "dice": float("nan"),
        "iou": float("nan"),
        "num_objects_gt": 0,
        "num_objects_pred": 0,
        "object_count_error": 0,
    }


def _save_mask(mask: np.ndarray, path: Path) -> None:
    from PIL import Image
    Image.fromarray(mask.astype(np.uint16)).save(path)


def _get_segmenter(method: str, **method_kwargs):
    """Return a callable (image, **params) -> labeled_mask for the chosen method."""
    if method == "watershed":
        return segment_watershed

    if method == "cellpose":
        try:
            from src.segment_cellpose import segment_cellpose
            return segment_cellpose
        except ImportError as exc:
            print(f"WARNING: cellpose not installed ({exc}). Falling back to watershed.")
            return segment_watershed

    if method in ("sam", "microsam"):
        checkpoint = method_kwargs.get("sam_checkpoint")
        sam_model = method_kwargs.get("sam_model_type", "vit_b")

        if method == "microsam":
            try:
                from src.segment_sam import segment_microsam
                def _microsam(image, **_kw):
                    return segment_microsam(image, model_type=sam_model,
                                            checkpoint_path=checkpoint)
                return _microsam
            except ImportError as exc:
                print(f"WARNING: micro_sam not installed ({exc}). Falling back to watershed.")
                return segment_watershed
        else:
            try:
                from src.segment_sam import segment_sam
                def _sam(image, **_kw):
                    return segment_sam(image, checkpoint_path=checkpoint,
                                       model_type=sam_model)
                return _sam
            except ImportError as exc:
                print(f"WARNING: segment_anything not installed ({exc}). Falling back to watershed.")
                return segment_watershed

    raise ValueError(f"Unknown segmentation method: {method!r}. "
                     "Choose from: watershed, cellpose, sam, microsam")


def _watershed_params(sigma, min_size, min_distance):
    return {
        "sigma": sigma,
        "min_size": min_size,
        "min_distance": min_distance,
        "use_contrast_enhancement": False,
    }


def run_pipeline(
    image_dir: str,
    mask_dir: str | None,
    output_dir: str,
    method: str = "watershed",
    sigma: float = 1.0,
    min_size: int = 30,
    min_distance: int = 5,
    auto_rerun: bool = False,
    llm_provider: str = "template",
    limit: int | None = None,
    sam_checkpoint: str | None = None,
    sam_model_type: str = "vit_b",
    cellpose_diameter: float | None = None,
    use_vit_qc: bool = False,
    vit_qc_model: str | None = None,
) -> None:
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)
    masks_out = output_dir / "masks"
    overlays_out = output_dir / "overlays"
    masks_out.mkdir(parents=True, exist_ok=True)
    overlays_out.mkdir(parents=True, exist_ok=True)

    pairs = match_image_mask_files(image_dir, mask_dir)
    if limit:
        pairs = pairs[:limit]

    # Build segmenter callable
    segmenter = _get_segmenter(
        method,
        sam_checkpoint=sam_checkpoint,
        sam_model_type=sam_model_type,
    )

    # Watershed-specific params (passed only when method == watershed or fallback)
    ws_params = _watershed_params(sigma, min_size, min_distance)
    # For non-watershed methods, segmenter ignores extra kwargs
    seg_params = ws_params if method == "watershed" else {}

    # Cellpose-specific params
    if method == "cellpose":
        seg_params = {"diameter": cellpose_diameter, "min_size": min_size}

    # ViT QC classifier
    vit_clf = None
    if use_vit_qc:
        from src.vit_qc import ViTQCClassifier
        if vit_qc_model and Path(vit_qc_model).exists():
            vit_clf = ViTQCClassifier().load(vit_qc_model)
            print(f"Loaded ViT QC classifier from {vit_qc_model}")
        else:
            print("Fitting ViT QC classifier on this dataset (rule-based pseudo-labels)…")
            all_images = [load_image(img_p) for img_p, _ in pairs]
            vit_clf = ViTQCClassifier()
            vit_clf.fit_from_images(all_images)
            if vit_qc_model:
                vit_clf.save(vit_qc_model)

    metrics_rows = []
    qc_rows = []
    agent_sections = []

    for img_path, mask_path in tqdm(pairs, desc=f"Processing images [{method}]"):
        name = img_path.name

        image = load_image(img_path)
        gt_mask = load_mask(mask_path) if mask_path else None

        t0 = time.perf_counter()
        pred_mask = segmenter(image, **seg_params)
        runtime = time.perf_counter() - t0

        metrics = evaluate_segmentation(gt_mask, pred_mask) if gt_mask is not None else _default_metrics()
        metrics["num_objects_pred"] = int(pred_mask.max())

        qc = run_qc(image, pred_mask)
        vit_result = predict_vit_qc(image, vit_clf)

        decision = analyze_segmentation(metrics, qc, ws_params)
        explanation = generate_agent_explanation(metrics, qc, decision, provider=llm_provider)

        best_mask = pred_mask
        best_metrics = metrics
        best_params = seg_params.copy()
        rerun_summary = ""

        # Auto-rerun is only available for watershed (param-tweaking is method-specific)
        if auto_rerun and decision["should_rerun"] and method == "watershed":
            new_params = decision["new_params"]
            t1 = time.perf_counter()
            rerun_mask = segment_watershed(image, **new_params)
            rerun_runtime = time.perf_counter() - t1

            rerun_metrics = evaluate_segmentation(gt_mask, rerun_mask) if gt_mask is not None else _default_metrics()
            rerun_metrics["num_objects_pred"] = int(rerun_mask.max())
            rerun_qc = run_qc(image, rerun_mask)

            if gt_mask is not None:
                kept_rerun = rerun_metrics["dice"] >= metrics["dice"]
            else:
                kept_rerun = not rerun_qc["has_many_tiny_objects"] and rerun_qc["num_objects_pred"] > 0

            if kept_rerun:
                best_mask = rerun_mask
                best_metrics = rerun_metrics
                best_params = new_params.copy()
                rerun_summary = (
                    f"Rerun improved Dice from {metrics['dice']:.2f} to {rerun_metrics['dice']:.2f}. "
                    "Kept rerun result."
                )
            else:
                rerun_summary = (
                    f"Rerun did not improve result (Dice {rerun_metrics['dice']:.2f} vs {metrics['dice']:.2f}). "
                    "Kept initial result."
                )

            metrics_rows.append({
                "image_name": name,
                "method": method,
                "run_type": "rerun",
                "sigma": new_params["sigma"],
                "min_size": new_params["min_size"],
                "min_distance": new_params["min_distance"],
                "use_contrast_enhancement": new_params.get("use_contrast_enhancement", False),
                **rerun_metrics,
                "runtime_seconds": round(rerun_runtime, 4),
                "kept_as_best": kept_rerun,
                "vit_qc_label": vit_result["label"],
                "vit_qc_pass_prob": round(vit_result["pass_prob"], 4),
                "vit_qc_backend": vit_result["backend"],
            })

            qc_rows.append({
                "image_name": name,
                "method": method,
                "run_type": "rerun",
                **{k: v for k, v in rerun_qc.items()},
            })

        kept_initial = best_mask is pred_mask
        metrics_rows.insert(0, {
            "image_name": name,
            "method": method,
            "run_type": "initial",
            "sigma": ws_params["sigma"],
            "min_size": ws_params["min_size"],
            "min_distance": ws_params["min_distance"],
            "use_contrast_enhancement": ws_params.get("use_contrast_enhancement", False),
            **metrics,
            "runtime_seconds": round(runtime, 4),
            "kept_as_best": kept_initial,
            "vit_qc_label": vit_result["label"],
            "vit_qc_pass_prob": round(vit_result["pass_prob"], 4),
            "vit_qc_backend": vit_result["backend"],
        })

        qc_rows.insert(0, {
            "image_name": name,
            "method": method,
            "run_type": "initial",
            **{k: v for k, v in qc.items()},
        })

        _save_mask(best_mask, masks_out / name)

        try:
            save_overlay(
                image,
                best_mask,
                gt_mask,
                overlays_out / (img_path.stem + "_overlay.png"),
                title=name,
            )
        except Exception as e:
            print(f"Warning: overlay failed for {name}: {e}")

        section = _format_agent_section(name, metrics, best_metrics, decision, explanation,
                                        rerun_summary, vit_result)
        agent_sections.append(section)

    pd.DataFrame(metrics_rows).to_csv(output_dir / "metrics.csv", index=False)
    pd.DataFrame(qc_rows).to_csv(output_dir / "qc_report.csv", index=False)

    report_lines = ["# Bioimage QC Agent Report\n"] + agent_sections
    (output_dir / "agent_report.md").write_text("\n".join(report_lines))

    print(f"\nDone. Results saved to {output_dir}/")


def _format_agent_section(
    name: str,
    initial_metrics: dict,
    best_metrics: dict,
    decision: dict,
    explanation: str,
    rerun_summary: str,
    vit_result: dict,
) -> str:
    dice_str = f"{initial_metrics['dice']:.2f}" if not _is_nan(initial_metrics["dice"]) else "N/A"
    iou_str = f"{initial_metrics['iou']:.2f}" if not _is_nan(initial_metrics["iou"]) else "N/A"

    lines = [
        f"## {name}",
        "",
        "### Initial result",
        f"- Dice: {dice_str}",
        f"- IoU: {iou_str}",
        f"- Predicted objects: {initial_metrics['num_objects_pred']}",
        f"- Ground truth objects: {initial_metrics['num_objects_gt']}",
        "",
        "### ViT QC",
        f"- Label: **{vit_result['label']}**",
        f"- Pass probability: {vit_result['pass_prob']:.2f}",
        f"- Backend: {vit_result['backend']}",
        "",
        "### Agent decision",
        f"- Status: {decision['status']}",
        f"- Failure mode: {decision['failure_mode']}",
        f"- Recommended action: {decision['recommended_action']}",
        "",
        "### Agent explanation",
        explanation,
    ]

    if rerun_summary:
        lines += ["", "### Final decision", rerun_summary]

    lines.append("")
    return "\n".join(lines)


def _is_nan(v) -> bool:
    try:
        import math
        return math.isnan(v)
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Bioimage QC Agent pipeline")
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--mask-dir", default=None)
    parser.add_argument("--output-dir", default="results")
    parser.add_argument(
        "--method",
        default="watershed",
        choices=["watershed", "cellpose", "sam", "microsam"],
        help="Segmentation method (default: watershed)",
    )
    # Watershed params
    parser.add_argument("--sigma", type=float, default=1.0)
    parser.add_argument("--min-size", type=int, default=30)
    parser.add_argument("--min-distance", type=int, default=5)
    parser.add_argument("--auto-rerun", action="store_true")
    # Cellpose params
    parser.add_argument("--cellpose-diameter", type=float, default=None,
                        help="Cellpose: expected nucleus diameter (None = auto)")
    # SAM params
    parser.add_argument("--sam-checkpoint", default=None,
                        help="Path to SAM/micro-SAM .pth checkpoint")
    parser.add_argument("--sam-model-type", default="vit_b",
                        choices=["vit_b", "vit_l", "vit_h",
                                 "vit_b_lm", "vit_l_lm",
                                 "vit_b_em_organelles"])
    # LLM
    parser.add_argument("--llm-provider", default="template",
                        choices=["template", "ollama", "gemini"])
    # ViT QC
    parser.add_argument("--vit-qc", action="store_true",
                        help="Run ViT-based QC classifier alongside rule-based QC")
    parser.add_argument("--vit-qc-model", default=None,
                        help="Path to save/load trained ViT QC classifier (.pkl)")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    run_pipeline(
        image_dir=args.image_dir,
        mask_dir=args.mask_dir,
        output_dir=args.output_dir,
        method=args.method,
        sigma=args.sigma,
        min_size=args.min_size,
        min_distance=args.min_distance,
        auto_rerun=args.auto_rerun,
        llm_provider=args.llm_provider,
        limit=args.limit,
        sam_checkpoint=args.sam_checkpoint,
        sam_model_type=args.sam_model_type,
        cellpose_diameter=args.cellpose_diameter,
        use_vit_qc=args.vit_qc,
        vit_qc_model=args.vit_qc_model,
    )


if __name__ == "__main__":
    main()
