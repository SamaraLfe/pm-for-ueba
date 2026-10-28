"""Обучение Alpha+/Inductive, token replay, точное исполнение и оценка на test."""

from conformance import exact_accept
from metrics import auc_metrics, metrics
import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data/prepared"
RESULTS = ROOT / "results"
RULES = ("host_60s", "host_300s", "host_session_30s")
MINERS = ("alpha_plus", "inductive")
os.environ["PM4PY_SHOW_PROGRESS_BAR"] = "False"
os.environ["PYTHONHASHSEED"] = "0"


def dump(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), "utf-8"
    )


def csv_save(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        w = csv.DictWriter(out, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def log_for(sequences):
    """Создать журнал PM4Py из последовательностей названий действий."""
    from pm4py.objects.log.obj import EventLog, Trace, Event

    return EventLog([Trace([Event({"concept:name": a}) for a in seq]) for seq in sequences])


def discover_model(training_cases, miner, rule=None):
    """Обучить сеть только на последовательностях обучающего раздела."""
    import pm4py
    from pm4py.algo.discovery.alpha import algorithm as alpha

    training_log = log_for(case["actions"] for case in training_cases)
    if miner == "alpha_plus":
        # Alpha+ изменяет вход: каждый запуск получает отдельный объект log.
        return alpha.apply(training_log, variant=alpha.Variants.ALPHA_VERSION_PLUS)
    selection = json.loads((RESULTS / "tuning/selection.json").read_text("utf-8"))
    noise_threshold = selection["selected"][rule]["noise_threshold"]
    return pm4py.discover_petri_net_inductive(training_log, noise_threshold=noise_threshold)


def worker(rule, miner):
    """Обучить одну сеть на train и проверить все три раздела журнала."""
    import pm4py
    from pm4py.algo.discovery.alpha import algorithm as alpha
    from pm4py.algo.conformance.tokenreplay import algorithm as replay

    started = time.monotonic()
    folder = RESULTS / rule / miner
    folder.mkdir(parents=True, exist_ok=True)
    cases = json.loads((DATA / rule / "cases.json").read_text("utf-8"))
    train = [c for c in cases if c["split"] == "train"]
    calibration = [c for c in cases if c["split"] == "calibration"]
    test = [c for c in cases if c["split"] == "test"]
    if not train or not calibration or (not test):
        raise ValueError("Empty partition")
    net, im, fm = discover_model(train, miner, rule)
    valid = (
        bool(im)
        and bool(fm)
        and set(im).issubset(net.places)
        and set(fm).issubset(net.places)
    )
    structure = {
        "places": len(net.places),
        "transitions": len(net.transitions),
        "hidden": sum((t.label is None for t in net.transitions)),
        "arcs": len(net.arcs),
        "isolated_visible": sum(
            (
                t.label is not None and (not t.in_arcs or not t.out_arcs)
                for t in net.transitions
            )
        ),
        "valid_markings": valid,
    }
    dump(
        folder / "markings.json",
        {
            "initial": [
                {"place": str(p.name), "tokens": n, "in_net": p in net.places}
                for p, n in im.items()
            ],
            "final": [
                {"place": str(p.name), "tokens": n, "in_net": p in net.places}
                for p, n in fm.items()
            ],
        },
    )
    if not valid:
        dump(
            folder / "result.json",
            {
                "rule": rule,
                "miner": miner,
                "status": "invalid_marking",
                "structure": structure,
            },
        )
        return
    for i, p in enumerate(sorted(net.places, key=lambda p: (str(p.name), id(p)))):
        p.name = f"p{i}"
    pm4py.write_pnml(net, im, fm, str(folder / "model.pnml"))
    imported = pm4py.read_pnml(str(folder / "model.pnml"))
    assert len(imported[0].places) == len(net.places) and len(imported[0].arcs) == len(
        net.arcs
    )
    net, im, fm = imported
    try:
        pm4py.save_vis_petri_net(net, im, fm, str(folder / "model.svg"), rankdir="LR")
    except Exception as error:
        (folder / "render_error.txt").write_text(str(error), "utf-8")
    visible = {t.label for t in net.transitions if t.label is not None}
    variants = list(
        dict.fromkeys((tuple(c["actions"]) for c in train + calibration + test))
    )
    checks = replay.apply(
        log_for(variants),
        net,
        im,
        fm,
        parameters={
            "show_progress_bar": False,
            "consider_remaining_in_fitness": True,
            "consider_activities_not_in_model_in_fitness": True,
        },
    )
    diagnostics = {}
    for index, (seq, check) in enumerate(zip(variants, checks)):
        unknown = sum((a not in visible for a in seq))
        fitness = min(1.0, max(0.0, float(check["trace_fitness"])))
        score = 1 - fitness * (1 - unknown / len(seq))
        diagnostics[seq] = {
            "token_fitness": fitness,
            "score": score,
            "unknown_activities": unknown,
            "missing_tokens": int(check["missing_tokens"]),
            "remaining_tokens": int(check["remaining_tokens"]),
            "exact_accept": exact_accept(seq, net, im, fm),
        }
        if index and index % 200 == 0:
            print(f"{rule}/{miner}: {index}/{len(variants)} variants", flush=True)
    val_scores = sorted((diagnostics[tuple(c["actions"])]["score"] for c in calibration))
    threshold = val_scores[max(0, math.ceil(0.99 * len(val_scores)) - 1)]
    rows = []
    for c in train + calibration + test:
        d = diagnostics[tuple(c["actions"])]
        rows.append(
            {
                "case_id": c["case_id"],
                "host": c["host"],
                "split": c["split"],
                "actual_attack": bool(c["attack_events"]),
                "steps": len(c["actions"]),
                "events": len(c["event_ids"]),
                "attack_events": c["attack_events"],
                "phases": ";".join(c["phases"]),
                "actions": " > ".join(c["actions"]),
                **d,
                "threshold": threshold,
                "predicted_attack": d["score"] > threshold + 1e-12,
            }
        )
    test_rows = [r for r in rows if r["split"] == "test"]
    result = {
        "rule": rule,
        "miner": miner,
        "status": "complete",
        "pm4py": pm4py.__version__,
        "structure": structure,
        "threshold": threshold,
        "threshold_rule": "score > calibration empirical 99th percentile",
        "train": metrics([r for r in rows if r["split"] == "train"]),
        "calibration": metrics([r for r in rows if r["split"] == "calibration"]),
        "test": metrics(test_rows),
        "variants": len(variants),
        "seconds": time.monotonic() - started,
        "per_phase": {
            str(p): {
                "attack_cases": sum((str(p) in r["phases"].split(";") for r in test_rows)),
                "detected_cases": sum(
                    (
                        str(p) in r["phases"].split(";") and r["predicted_attack"]
                        for r in test_rows
                    )
                ),
            }
            for p in range(1, 6)
        },
        "internal_test": metrics([r for r in test_rows if r["host"].startswith("172.16.")]),
        "external_anchor_test": metrics(
            [r for r in test_rows if not r["host"].startswith("172.16.")]
        ),
    }
    csv_save(folder / "trace_diagnostics.csv", rows)
    examples = []
    for outcome in ("TP", "FP", "FN", "TN"):
        group = [
            r
            for r in test_rows
            if ("T" if r["actual_attack"] == r["predicted_attack"] else "F")
            + ("P" if r["predicted_attack"] else "N")
            == outcome
        ]
        examples.extend(
            (
                {"outcome": outcome, **r}
                for r in sorted(group, key=lambda r: r["score"], reverse=True)[:3]
            )
        )
    dump(folder / "examples.json", examples)
    dump(folder / "result.json", result)
    print(
        json.dumps(
            {"rule": rule, "miner": miner, "threshold": threshold, "test": result["test"]},
            ensure_ascii=False,
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=2)
    args = parser.parse_args()
    RESULTS.mkdir(exist_ok=True)
    if args.worker:
        return worker(*args.worker)
    results = []
    for rule in RULES:
        for miner in MINERS:
            print(f"Model: {rule}/{miner}", flush=True)
            folder = RESULTS / rule / miner
            folder.mkdir(parents=True, exist_ok=True)
            try:
                with (folder / "run.log").open("w", encoding="utf-8") as log:
                    run = subprocess.run(
                        [sys.executable, __file__, "--worker", rule, miner],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=300,
                        env={
                            **os.environ,
                            "PYTHONIOENCODING": "utf-8",
                            "PYTHONHASHSEED": "0",
                        },
                    )
                if run.returncode:
                    raise RuntimeError(f"Worker exit {run.returncode}; see run.log")
                result = json.loads((folder / "result.json").read_text("utf-8"))
            except Exception as error:
                result = {
                    "rule": rule,
                    "miner": miner,
                    "status": "failed",
                    "error": str(error),
                }
                dump(folder / "result.json", result)
            results.append(result)
            print(f"Finished: {rule}/{miner}: {result['status']}", flush=True)
            dump(RESULTS / "summary.json", results)
    csv_save(
        RESULTS / "comparison.csv",
        [
            {
                "aggregation": r["rule"],
                "miner": r["miner"],
                "threshold": r["threshold"],
                **r["test"],
            }
            for r in results
            if r["status"] == "complete"
        ],
    )


if __name__ == "__main__":
    main()
