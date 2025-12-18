"""
Generate results/dashboard.html — a fully self-contained interactive Plotly dashboard.
Run:  python make_dashboard.py
"""

import base64, warnings
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from PIL import Image as PILImage

warnings.filterwarnings("ignore")

# Colour palette shared across the whole dashboard
METHOD_COLORS = {
    "watershed": "#3498db",
    "cellpose":  "#e67e22",
    "sam":       "#27ae60",
    "microsam":  "#8e44ad",
}


def b64_png(path: Path) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def load_results():
    df_m = pd.read_csv("results/metrics.csv")
    df_q = pd.read_csv("results/qc_report.csv")
    initial = df_m[df_m.run_type == "initial"].copy()
    rerun   = df_m[df_m.run_type == "rerun"].copy()
    init_q  = df_q[df_q.run_type == "initial"].copy()
    return df_m, df_q, initial, rerun, init_q


def make_html():
    df_m, df_q, initial, rerun, init_q = load_results()

    combined = initial.merge(
        init_q[["image_name", "blur_score", "contrast_score",
                "tiny_object_fraction", "is_blurry", "is_low_contrast", "has_many_tiny_objects"]],
        on="image_name",
    )
    combined["short_name"] = combined["image_name"].str[:20] + "…"
    combined["status"] = combined["dice"].apply(
        lambda d: "pass (≥0.8)" if d >= 0.8 else ("low (0.5–0.8)" if d >= 0.5 else "poor (<0.5)")
    )

    # Which segmentation methods are present in the results?
    methods_present = (
        initial["method"].unique().tolist() if "method" in initial.columns else ["watershed"]
    )

    # ── colours ──────────────────────────────────────────────────────────────
    STATUS_COLORS = {"pass (≥0.8)": "#27ae60", "low (0.5–0.8)": "#f39c12", "poor (<0.5)": "#e74c3c"}

    sections = []

    # ── 1. KPI strip ─────────────────────────────────────────────────────────
    n_pass   = (initial["dice"] >= 0.8).sum()
    n_low    = ((initial["dice"] >= 0.5) & (initial["dice"] < 0.8)).sum()
    n_poor   = (initial["dice"] < 0.5).sum()
    n_rerun  = len(rerun)
    avg_dice = initial["dice"].mean()
    avg_iou  = initial["iou"].mean()

    fig_kpi = go.Figure()
    kpis = [
        ("Images processed", "50", "#2980b9"),
        ("Avg Dice", f"{avg_dice:.3f}", "#27ae60"),
        ("Avg IoU",  f"{avg_iou:.3f}",  "#8e44ad"),
        ("Pass (Dice≥0.8)", str(n_pass), "#27ae60"),
        ("Low (0.5–0.8)",   str(n_low),  "#f39c12"),
        ("Poor (<0.5)",     str(n_poor), "#e74c3c"),
        ("Agent reruns",    str(n_rerun),"#16a085"),
    ]
    for i, (label, val, color) in enumerate(kpis):
        fig_kpi.add_trace(go.Indicator(
            mode="number",
            value=float(val) if val.replace(".","").isdigit() else None,
            number={"valueformat": "" if "." in val else "d",
                    "font": {"size": 36, "color": color}},
            title={"text": label, "font": {"size": 13}},
            domain={"column": i, "row": 0},
        ))
        if not val.replace(".","").isdigit():
            fig_kpi.add_annotation(
                x=(i + 0.5) / len(kpis), y=0.5, text=f"<b>{val}</b>",
                font=dict(size=36, color=color), showarrow=False,
                xref="paper", yref="paper"
            )
    fig_kpi.update_layout(
        grid={"rows": 1, "columns": len(kpis), "pattern": "independent"},
        height=140, margin=dict(t=20, b=10, l=10, r=10),
        paper_bgcolor="#f8f9fa",
    )
    sections.append(("<h2 style='font-family:sans-serif;color:#2c3e50;margin:20px 0 0 20px'>"
                     "Bioimage QC Agent — Results Dashboard</h2>", ""))
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Summary</h3>",
                     fig_kpi.to_html(full_html=False, include_plotlyjs=False)))

    # ── 2. Dice histogram + status pie ───────────────────────────────────────
    fig_hist = make_subplots(rows=1, cols=2,
                              subplot_titles=("Dice score distribution", "Pass/Fail breakdown"),
                              specs=[[{"type": "xy"}, {"type": "domain"}]])
    fig_hist.add_trace(go.Histogram(
        x=initial["dice"].dropna(), nbinsx=15,
        marker_color="#3498db", opacity=0.85, name="Dice",
    ), row=1, col=1)
    fig_hist.add_vline(x=avg_dice, line_dash="dash", line_color="red",
                       annotation_text=f"mean={avg_dice:.3f}", row=1, col=1)
    fig_hist.add_vline(x=0.65, line_dash="dot", line_color="orange",
                       annotation_text="QC threshold (0.65)", row=1, col=1)
    pie_labels = ["pass (≥0.8)", "low (0.5–0.8)", "poor (<0.5)"]
    pie_values = [n_pass, n_low, n_poor]
    pie_colors = [STATUS_COLORS[l] for l in pie_labels]
    fig_hist.add_trace(go.Pie(labels=pie_labels, values=pie_values,
                               marker_colors=pie_colors, textinfo="label+value+percent",
                               hole=0.35), row=1, col=2)
    fig_hist.update_layout(height=380, showlegend=False,
                            margin=dict(t=50, b=20, l=20, r=20))
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Dice Score Distribution</h3>",
                     fig_hist.to_html(full_html=False, include_plotlyjs=False)))

    # ── 3. Predicted vs GT scatter ───────────────────────────────────────────
    fig_scatter = px.scatter(
        combined, x="num_objects_gt", y="num_objects_pred",
        color="dice", color_continuous_scale="RdYlGn", range_color=[0, 1],
        hover_name="short_name",
        hover_data={"dice": ":.3f", "iou": ":.3f",
                    "num_objects_gt": True, "num_objects_pred": True},
        labels={"num_objects_gt": "GT object count", "num_objects_pred": "Predicted count",
                "dice": "Dice"},
        title="Predicted vs Ground Truth Object Count  (color = Dice)",
        size_max=12,
    )
    max_obj = max(combined["num_objects_gt"].max(), combined["num_objects_pred"].max())
    fig_scatter.add_trace(go.Scatter(x=[0, max_obj], y=[0, max_obj], mode="lines",
                                      line=dict(dash="dash", color="black", width=1),
                                      name="perfect", showlegend=True))
    fig_scatter.update_layout(height=420, margin=dict(t=50, b=20, l=20, r=20))
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Object Count Accuracy</h3>",
                     fig_scatter.to_html(full_html=False, include_plotlyjs=False)))

    # ── 4. QC flags bar ──────────────────────────────────────────────────────
    flag_df = pd.DataFrame({
        "Flag": ["Blurry", "Low contrast", "Tiny objects"],
        "Count": [init_q["is_blurry"].sum(), init_q["is_low_contrast"].sum(), init_q["has_many_tiny_objects"].sum()],
        "Color": ["#e74c3c", "#f39c12", "#8e44ad"],
    })
    fig_flags = go.Figure(go.Bar(
        x=flag_df["Flag"], y=flag_df["Count"],
        marker_color=flag_df["Color"], text=flag_df["Count"],
        textposition="outside",
    ))
    fig_flags.update_layout(title="QC Flags (out of 50 images)",
                             yaxis_range=[0, 55], height=340,
                             margin=dict(t=50, b=20, l=20, r=20))
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>QC Flags</h3>",
                     fig_flags.to_html(full_html=False, include_plotlyjs=False)))

    # ── 5. QC feature correlation ────────────────────────────────────────────
    fig_corr = make_subplots(rows=1, cols=3,
                              subplot_titles=("Blur score vs Dice",
                                              "Contrast score vs Dice",
                                              "Tiny object fraction vs Dice"))
    colors = ["#3498db", "#e67e22", "#8e44ad"]
    for col_idx, (feat, color) in enumerate(zip(
        ["blur_score", "contrast_score", "tiny_object_fraction"], colors
    ), start=1):
        corr = combined[[feat, "dice"]].corr().iloc[0, 1]
        fig_corr.add_trace(go.Scatter(
            x=combined[feat], y=combined["dice"], mode="markers",
            marker=dict(color=color, size=7, opacity=0.75,
                        line=dict(color="gray", width=0.3)),
            text=combined["short_name"],
            name=feat,
            hovertemplate=f"%{{text}}<br>{feat}: %{{x:.3f}}<br>Dice: %{{y:.3f}}<extra></extra>",
        ), row=1, col=col_idx)
        xref = "x domain" if col_idx == 1 else f"x{col_idx} domain"
        yref = "y domain" if col_idx == 1 else f"y{col_idx} domain"
        fig_corr.add_annotation(
            x=0.5, y=0.05, xref=xref, yref=yref,
            text=f"r = {corr:.3f}", showarrow=False, font=dict(size=12, color="black"),
        )
    fig_corr.update_layout(height=380, showlegend=False,
                            margin=dict(t=60, b=20, l=20, r=20))
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>QC Feature Correlations</h3>",
                     fig_corr.to_html(full_html=False, include_plotlyjs=False)))

    # ── 6. Rerun comparison ──────────────────────────────────────────────────
    if len(rerun) > 0:
        merged = initial.merge(rerun, on="image_name", suffixes=("_initial", "_rerun"), how="inner")
        merged["short"] = merged["image_name"].str[:22] + "…"
        merged["improved"] = merged["dice_rerun"] > merged["dice_initial"]
        merged["marker_color"] = merged["improved"].map({True: "#27ae60", False: "#e74c3c"})

        fig_rerun = go.Figure()
        fig_rerun.add_trace(go.Scatter(
            x=merged["dice_initial"], y=merged["dice_rerun"],
            mode="markers", text=merged["short"],
            hovertemplate="%{text}<br>Initial: %{x:.3f} → Rerun: %{y:.3f}<extra></extra>",
            marker=dict(color=merged["marker_color"], size=9,
                        line=dict(color="gray", width=0.4)),
            name="images",
        ))
        fig_rerun.add_trace(go.Scatter(x=[0,1], y=[0,1], mode="lines",
                                        line=dict(dash="dash", color="black", width=1),
                                        name="no change"))
        fig_rerun.update_layout(
            title=(f"Agent rerun impact on {len(merged)} flagged images  "
                   f"(green = improved, red = not improved)"),
            xaxis_title="Dice — initial", yaxis_title="Dice — after rerun",
            xaxis_range=[0, 1], yaxis_range=[0, 1],
            height=430, margin=dict(t=60, b=20, l=20, r=20),
        )
        sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Agent Rerun Impact</h3>",
                         fig_rerun.to_html(full_html=False, include_plotlyjs=False)))

    # ── 7. Per-image Dice bar chart ──────────────────────────────────────────
    sorted_df = combined.sort_values("dice", ascending=False).reset_index(drop=True)
    bar_colors = [STATUS_COLORS[s] for s in sorted_df["status"]]
    fig_bar = go.Figure(go.Bar(
        x=sorted_df.index + 1,
        y=sorted_df["dice"],
        marker_color=bar_colors,
        text=sorted_df["short_name"],
        hovertemplate="%{text}<br>Dice: %{y:.3f}<extra></extra>",
    ))
    fig_bar.add_hline(y=0.8, line_dash="dot", line_color="#27ae60", annotation_text="0.8")
    fig_bar.add_hline(y=0.5, line_dash="dot", line_color="#e74c3c", annotation_text="0.5")
    fig_bar.update_layout(
        title="Per-image Dice score (sorted, green ≥ 0.8, orange 0.5–0.8, red < 0.5)",
        xaxis_title="Image rank", yaxis_title="Dice",
        yaxis_range=[0, 1.05], height=400, showlegend=False,
        margin=dict(t=60, b=40, l=40, r=20),
    )
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Per-Image Dice Scores</h3>",
                     fig_bar.to_html(full_html=False, include_plotlyjs=False)))

    # ── 8. ViT QC classifier results ─────────────────────────────────────────
    vit_cols = [c for c in initial.columns if c.startswith("vit_qc_")]
    if "vit_qc_pass_prob" in initial.columns:
        has_vit = initial["vit_qc_pass_prob"].notna().any()
    else:
        has_vit = False

    if has_vit:
        vit_df = initial[["image_name", "short_name", "dice",
                           "vit_qc_label", "vit_qc_pass_prob", "vit_qc_backend"]].dropna(
            subset=["vit_qc_pass_prob"]
        ).copy()

        # Pass-prob histogram
        fig_vit_hist = go.Figure()
        fig_vit_hist.add_trace(go.Histogram(
            x=vit_df["vit_qc_pass_prob"], nbinsx=15,
            marker_color="#16a085", opacity=0.85, name="Pass probability",
        ))
        fig_vit_hist.add_vline(x=0.5, line_dash="dash", line_color="red",
                                annotation_text="threshold (0.5)")
        fig_vit_hist.update_layout(
            title="ViT QC — pass-probability distribution",
            xaxis_title="Pass probability", yaxis_title="Count",
            height=320, margin=dict(t=50, b=20, l=20, r=20),
        )

        # Scatter: ViT pass-prob vs Dice
        fig_vit_scatter = px.scatter(
            vit_df, x="vit_qc_pass_prob", y="dice",
            color="vit_qc_label",
            color_discrete_map={"pass": "#27ae60", "fail": "#e74c3c"},
            hover_name="short_name",
            hover_data={"dice": ":.3f", "vit_qc_pass_prob": ":.3f"},
            labels={"vit_qc_pass_prob": "ViT pass probability", "dice": "Dice score"},
            title="ViT QC pass probability vs Dice score",
        )
        fig_vit_scatter.add_vline(x=0.5, line_dash="dot", line_color="gray")
        fig_vit_scatter.add_hline(y=0.65, line_dash="dot", line_color="gray")
        fig_vit_scatter.update_layout(height=380, margin=dict(t=50, b=20, l=20, r=20))

        # Agreement with rule-based QC
        vit_pass = (vit_df["vit_qc_pass_prob"] >= 0.5).astype(int)
        rule_pass = (combined.set_index("image_name")
                     .reindex(vit_df["image_name"])["dice"].values >= 0.65).astype(int)
        agree = int((vit_pass.values == rule_pass).sum())
        n = len(vit_pass)

        agree_html = (
            f"<div style='font-family:sans-serif;font-size:14px;padding:8px 20px;"
            f"background:white;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);"
            f"margin:6px 20px;display:inline-block'>"
            f"ViT QC agreement with Dice≥0.65 rule: "
            f"<b>{agree}/{n} ({100*agree/n:.0f}%)</b> &nbsp;|&nbsp; "
            f"Backend: <b>{vit_df['vit_qc_backend'].iloc[0]}</b>"
            f"</div>"
        )

        vit_html = (
            fig_vit_hist.to_html(full_html=False, include_plotlyjs=False)
            + fig_vit_scatter.to_html(full_html=False, include_plotlyjs=False)
            + agree_html
        )
        sections.append((
            "<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>ViT QC Classifier</h3>",
            vit_html,
        ))

    # ── 9. Method comparison ─────────────────────────────────────────────────
    if len(methods_present) > 1:
        fig_method = go.Figure()
        for meth in methods_present:
            sub = initial[initial["method"] == meth] if "method" in initial.columns else initial
            color = METHOD_COLORS.get(meth, "#aaa")
            fig_method.add_trace(go.Box(
                y=sub["dice"].dropna(),
                name=meth,
                marker_color=color,
                boxpoints="all",
                jitter=0.3,
                pointpos=-1.5,
            ))
        fig_method.update_layout(
            title="Dice score by segmentation method",
            yaxis_title="Dice",
            yaxis_range=[0, 1.05],
            height=420,
            margin=dict(t=60, b=20, l=40, r=20),
            showlegend=True,
        )
        sections.append((
            "<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Method Comparison</h3>",
            fig_method.to_html(full_html=False, include_plotlyjs=False),
        ))

    # ── 10. Benchmark comparison (static — from 5-image experiment) ─────────────
    benchmark_methods = ['Watershed', 'SAM vit_b', 'micro-SAM vit_b_lm', 'Cellpose']
    benchmark_dice    = [0.380, 0.251, 0.089, 0.888]
    bench_colors      = [
        METHOD_COLORS.get('watershed', '#3498db'),
        METHOD_COLORS.get('sam', '#27ae60'),
        METHOD_COLORS.get('microsam', '#8e44ad'),
        METHOD_COLORS.get('cellpose', '#e67e22'),
    ]
    fig_bench = go.Figure(go.Bar(
        x=benchmark_methods,
        y=benchmark_dice,
        marker_color=bench_colors,
        text=[f'{v:.3f}' for v in benchmark_dice],
        textposition='outside',
    ))
    fig_bench.update_layout(
        title='Method benchmark — mean Dice (5 BBBC038 images)',
        yaxis_title='Mean Dice',
        yaxis_range=[0, 1.05],
        height=380,
        margin=dict(t=60, b=20, l=40, r=20),
    )
    sections.append((
        "<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Method Benchmark</h3>",
        fig_bench.to_html(full_html=False, include_plotlyjs=False)
        + "<p style='font-family:sans-serif;font-size:12px;color:#777;margin:4px 20px'>"
          "Benchmark on 5 fluorescence nuclei images (BBBC038). "
          "micro-SAM score is in automatic mask generation (AMG) mode; "
          "interactive mode yields higher accuracy.</p>",
    ))

    # ── 11. Sample overlay gallery ────────────────────────────────────────────
    overlay_files = sorted(Path("results/overlays").glob("*.png"))[:9]
    gallery_rows = []
    for i in range(0, len(overlay_files), 3):
        row_html = "<div style='display:flex;flex-wrap:wrap;gap:8px;margin:10px 20px;'>"
        for ov in overlay_files[i : i + 3]:
            img_name = ov.stem.replace("_overlay", "") + ".png"
            row_m = initial[initial.image_name == img_name]
            dice_val = f"{row_m['dice'].values[0]:.3f}" if len(row_m) else "N/A"
            status_row = combined[combined.image_name == img_name]
            st = status_row["status"].values[0] if len(status_row) else ""
            border_color = STATUS_COLORS.get(st, "#aaa")
            b64 = b64_png(ov)
            row_html += (
                f"<div style='text-align:center;border:2px solid {border_color};border-radius:6px;padding:4px'>"
                f"<img src='{b64}' style='max-width:340px;max-height:220px;display:block'/>"
                f"<div style='font-family:monospace;font-size:11px;margin-top:4px'>"
                f"{img_name[:28]}…<br><b>Dice: {dice_val}</b></div></div>"
            )
        row_html += "</div>"
        gallery_rows.append(row_html)
    sections.append(("<h3 style='font-family:sans-serif;color:#555;margin:10px 0 0 20px'>Sample Overlay Gallery</h3>",
                     "\n".join(gallery_rows)))

    # ── Assemble HTML ─────────────────────────────────────────────────────────
    plotlyjs = "<script src='https://cdn.plot.ly/plotly-2.32.0.min.js'></script>"
    body = ""
    for header_html, content_html in sections:
        body += header_html + "\n" + content_html + "\n"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Bioimage QC Agent — Dashboard</title>
{plotlyjs}
<style>
  body {{ font-family: sans-serif; background: #f0f2f5; margin: 0; padding: 0 0 40px; }}
  h2, h3 {{ color: #2c3e50; }}
  .plotly-graph-div {{ background: white; border-radius: 8px;
    box-shadow: 0 1px 4px rgba(0,0,0,.12); margin: 6px 20px; }}
</style>
</head>
<body>
{body}
<div style='text-align:center;color:#aaa;font-size:12px;margin-top:30px'>
  Generated by Bioimage QC Agent &mdash; 2018 Data Science Bowl (BBBC038) subset
</div>
</body>
</html>
"""
    out = Path("results/dashboard.html")
    out.write_text(html)
    print(f"Dashboard saved to {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    make_html()
