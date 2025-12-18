def analyze_segmentation(metrics: dict, qc: dict, params: dict) -> dict:
    new_params = params.copy()
    reasons = []
    actions = []

    if metrics["dice"] < 0.65:
        reasons.append("low Dice score")

    if metrics["num_objects_pred"] > 1.5 * max(metrics["num_objects_gt"], 1):
        reasons.append("over-segmentation")
        actions.append("increase smoothing")
        actions.append("increase minimum object size")
        new_params["sigma"] = params["sigma"] + 0.5
        new_params["min_size"] = params["min_size"] * 2
    elif metrics["num_objects_gt"] > 0 and metrics["num_objects_pred"] < 0.6 * metrics["num_objects_gt"]:
        reasons.append("under-segmentation")
        actions.append("reduce smoothing")
        actions.append("decrease minimum object size")
        new_params["sigma"] = max(0.5, params["sigma"] - 0.5)
        new_params["min_size"] = max(10, params["min_size"] // 2)

    if qc["tiny_object_fraction"] > 0.25:
        reasons.append("many tiny objects")
        actions.append("remove small objects more aggressively")
        new_params["min_size"] = max(new_params["min_size"], params["min_size"] * 2)

    if qc["is_low_contrast"]:
        reasons.append("low contrast")
        actions.append("try contrast enhancement")
        new_params["use_contrast_enhancement"] = True

    if qc["is_blurry"]:
        reasons.append("blurry image")
        actions.append("flag for manual review")

    if not reasons:
        return {
            "status": "pass",
            "failure_mode": "none",
            "recommended_action": "keep current segmentation",
            "new_params": params,
            "should_rerun": False,
            "reasons": [],
        }

    return {
        "status": "needs_review",
        "failure_mode": ", ".join(reasons),
        "recommended_action": "; ".join(actions) if actions else "manual review",
        "new_params": new_params,
        "should_rerun": new_params != params,
        "reasons": reasons,
    }
