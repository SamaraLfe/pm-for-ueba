"""Контроль: вклад неизвестных действий и отдельный token fitness."""

import csv
import json
import math
from pathlib import Path
from train_models import metrics, dump, csv_save

ROOT = Path(__file__).resolve().parent


def main():
    summary = json.loads((ROOT / "results/summary.json").read_text("utf-8"))
    output = []
    for model in summary:
        if model["status"] != "complete":
            continue
        folder = ROOT / "results" / model["rule"] / model["miner"]
        with (folder / "trace_diagnostics.csv").open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for k in ("actual_attack", "predicted_attack"):
                r[k] = r[k] == "True"
            for k in ("score", "token_fitness"):
                r[k] = float(r[k])
            for k in ("unknown_activities", "steps"):
                r[k] = int(r[k])
            r["exact_accept"] = {"True": True, "False": False, "": None}[r["exact_accept"]]
        variants = {}
        train_vocabulary = {
            a for r in rows if r["split"] == "train" for a in r["actions"].split(" > ")
        }
        for name, score_fn in [
            ("unknown_only", lambda r: r["unknown_activities"] / r["steps"]),
            (
                "train_unknown",
                lambda r: sum(
                    (a not in train_vocabulary for a in r["actions"].split(" > "))
                )
                / r["steps"],
            ),
            ("token_only", lambda r: 1 - r["token_fitness"]),
        ]:
            calibration = sorted((score_fn(r) for r in rows if r["split"] == "calibration"))
            threshold = calibration[max(0, math.ceil(0.99 * len(calibration)) - 1)]
            test = [
                {
                    **r,
                    "score": score_fn(r),
                    "predicted_attack": score_fn(r) > threshold + 1e-12,
                }
                for r in rows
                if r["split"] == "test"
            ]
            variants[name] = {"threshold": threshold, **metrics(test)}
            output.append(
                {
                    "aggregation": model["rule"],
                    "miner": model["miner"],
                    "score_type": name,
                    **variants[name],
                }
            )
        variants["known_actions_only"] = metrics(
            [r for r in rows if r["split"] == "test" and r["unknown_activities"] == 0]
        )
        variants["detected_attacks_with_unknown"] = sum(
            (
                r["split"] == "test"
                and r["actual_attack"]
                and r["predicted_attack"]
                and (r["unknown_activities"] > 0)
                for r in rows
            )
        )
        model["controls"] = variants
        dump(folder / "result.json", model)
    dump(ROOT / "results/summary.json", summary)
    csv_save(ROOT / "results/controls.csv", output)


if __name__ == "__main__":
    main()
