import os


def generate_template_explanation(metrics: dict, qc: dict, decision: dict) -> str:
    lines = []
    status = decision["status"]

    if status == "pass":
        lines.append(
            f"The segmentation passed QC with a Dice score of {metrics['dice']:.2f} "
            f"and IoU of {metrics['iou']:.2f}."
        )
        lines.append(
            f"The predicted object count ({metrics['num_objects_pred']}) is close to "
            f"the ground truth ({metrics['num_objects_gt']})."
        )
        lines.append("No significant issues were detected. The current parameters are appropriate.")
    else:
        lines.append(
            f"The segmentation needs review. Dice score is {metrics['dice']:.2f} "
            f"and IoU is {metrics['iou']:.2f}."
        )
        lines.append(f"Detected failure mode(s): {decision['failure_mode']}.")

        if "over-segmentation" in decision["failure_mode"]:
            lines.append(
                f"The predicted count ({metrics['num_objects_pred']}) is much higher than "
                f"the ground truth ({metrics['num_objects_gt']}), indicating over-segmentation."
            )
        elif "under-segmentation" in decision["failure_mode"]:
            lines.append(
                f"The predicted count ({metrics['num_objects_pred']}) is much lower than "
                f"the ground truth ({metrics['num_objects_gt']}), indicating under-segmentation."
            )

        if qc["is_blurry"]:
            lines.append(
                f"The image appears blurry (blur score: {qc['blur_score']:.2f}), "
                "which may reduce segmentation accuracy."
            )

        if qc["is_low_contrast"]:
            lines.append(
                f"Low contrast detected (contrast score: {qc['contrast_score']:.3f}). "
                "Contrast enhancement may help."
            )

        if decision["recommended_action"] != "manual review":
            lines.append(f"Recommended action: {decision['recommended_action']}.")

    return " ".join(lines)


def explain_with_ollama(
    metrics: dict,
    qc: dict,
    decision: dict,
    model: str = "qwen2.5:3b",
) -> str:
    try:
        import requests

        prompt = _build_prompt(metrics, qc, decision)
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception:
        return generate_template_explanation(metrics, qc, decision)


def explain_with_gemini(
    metrics: dict,
    qc: dict,
    decision: dict,
    api_key: str | None = None,
    model: str = "gemini-1.5-flash",
) -> str:
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return generate_template_explanation(metrics, qc, decision)
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        client = genai.GenerativeModel(model)
        prompt = _build_prompt(metrics, qc, decision)
        result = client.generate_content(prompt)
        return result.text.strip()
    except Exception:
        return generate_template_explanation(metrics, qc, decision)


def generate_agent_explanation(
    metrics: dict,
    qc: dict,
    decision: dict,
    provider: str = "template",
    api_key: str | None = None,
) -> str:
    if provider == "ollama":
        return explain_with_ollama(metrics, qc, decision)
    if provider == "gemini":
        return explain_with_gemini(metrics, qc, decision, api_key=api_key)
    return generate_template_explanation(metrics, qc, decision)


def _build_prompt(metrics: dict, qc: dict, decision: dict) -> str:
    return (
        "You are a bioimage analysis assistant. "
        "Explain the segmentation QC decision in 3 to 5 sentences. "
        "Do not invent metrics. Use only the provided metrics, QC values, and rule-based decision. "
        "Explain the likely failure mode and recommended next action.\n\n"
        f"Metrics: Dice={metrics['dice']:.3f}, IoU={metrics['iou']:.3f}, "
        f"predicted objects={metrics['num_objects_pred']}, "
        f"ground truth objects={metrics['num_objects_gt']}.\n"
        f"QC: blur_score={qc['blur_score']:.2f}, contrast_score={qc['contrast_score']:.3f}, "
        f"tiny_object_fraction={qc['tiny_object_fraction']:.2f}, "
        f"is_blurry={qc['is_blurry']}, is_low_contrast={qc['is_low_contrast']}.\n"
        f"Decision: status={decision['status']}, "
        f"failure_mode={decision['failure_mode']}, "
        f"recommended_action={decision['recommended_action']}."
    )
