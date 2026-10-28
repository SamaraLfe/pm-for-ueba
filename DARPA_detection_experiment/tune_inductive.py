"""Выбор noise_threshold без доступа к test и его attack-меткам."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
import pm4py
from pm4py.algo.conformance.tokenreplay import algorithm as replay
from pm4py.algo.evaluation.precision import algorithm as precision
from io_utils import load_json, save_csv, save_json
from train_models import ROOT, RULES, log_for

GRID = (0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5)


def corrected_fitness(cases, net, initial, final):
    """Не считать неизвестные модели действия полностью соответствующими."""
    visible = {t.label for t in net.transitions if t.label is not None}
    checks = replay.apply(
        log_for(c["actions"] for c in cases),
        net,
        initial,
        final,
        parameters={
            "show_progress_bar": False,
            "consider_remaining_in_fitness": True,
            "consider_activities_not_in_model_in_fitness": True,
        },
    )
    values = []
    for case, check in zip(cases, checks):
        unknown = sum(action not in visible for action in case["actions"])
        values.append(float(check["trace_fitness"]) * (1 - unknown / len(case["actions"])))
    return sum(values) / len(values)


def harmonic(values):
    return len(values) / sum(1 / value for value in values) if all(values) else 0


def worker(rule):
    # Test не включается даже в последовательности для подбора.
    cases = load_json(ROOT / "data/prepared" / rule / "cases.json")
    train = [c for c in cases if c["split"] == "train"]
    calibration = [c for c in cases if c["split"] == "calibration"]
    results = []
    for noise in GRID:
        net, initial, final = pm4py.discover_petri_net_inductive(
            log_for(c["actions"] for c in train), noise_threshold=noise
        )
        train_fitness = corrected_fitness(train, net, initial, final)
        calibration_fitness = corrected_fitness(calibration, net, initial, final)
        model_precision = float(
            precision.apply(
                log_for(c["actions"] for c in train),
                net,
                initial,
                final,
                variant=precision.Variants.ETCONFORMANCE_TOKEN,
                parameters={"show_progress_bar": False},
            )
        )
        quality = harmonic((train_fitness, calibration_fitness, model_precision))
        results.append(
            {
                "aggregation": rule,
                "noise_threshold": noise,
                "train_corrected_fitness": train_fitness,
                "calibration_corrected_fitness": calibration_fitness,
                "et_precision_train": model_precision,
                "quality": quality,
                "eligible": train_fitness >= 0.95 and calibration_fitness >= 0.90,
                "places": len(net.places),
                "transitions": len(net.transitions),
                "arcs": len(net.arcs),
                "train_cases": len(train),
                "calibration_cases": len(calibration),
                "multi_step_train_fraction": sum(len(c["actions"]) >= 2 for c in train)
                / len(train),
            }
        )
        print(rule, noise, round(quality, 4), results[-1]["eligible"], flush=True)
    save_json(ROOT / "results/tuning" / f"{rule}.json", results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument(
        "--reuse", action="store_true", help="Повторить выбор по уже рассчитанной сетке"
    )
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    folder = ROOT / "results/tuning"
    folder.mkdir(parents=True, exist_ok=True)
    all_candidates = []
    selected = {}
    for rule in RULES:
        if not args.reuse:
            with (folder / f"{rule}.log").open("w", encoding="utf-8") as output:
                subprocess.run(
                    [sys.executable, __file__, "--worker", rule],
                    check=True,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=240,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"},
                )
        candidates = load_json(folder / f"{rule}.json")
        all_candidates.extend(candidates)
        eligible = [candidate for candidate in candidates if candidate["eligible"]]
        if not eligible:
            raise ValueError(f"Нет модели с заданным покрытием train/calibration: {rule}")
        best = max(
            eligible, key=lambda c: (c["quality"], -c["arcs"], -c["noise_threshold"])
        )
        selected[rule] = dict(best)
        print("Selected:", rule, best["noise_threshold"], best["quality"], flush=True)
    # Для объяснения нужен и ограничивающий процесс, и многошаговый контекст.
    # Ранжирование не использует test, его метки, F1 или recall.
    for choice in selected.values():
        choice["explanation_suitability"] = (
            choice["quality"] * choice["multi_step_train_fraction"]
        )
    explanation = max(
        selected.values(), key=lambda c: (c["explanation_suitability"], -c["arcs"])
    )
    save_json(
        folder / "selection.json",
        {
            "grid": list(GRID),
            "train_min_fitness": 0.95,
            "calibration_min_fitness": 0.90,
            "criterion": "harmonic(train_corrected_fitness, calibration_corrected_fitness, et_precision_train)",
            "aggregation_criterion": "quality * multi_step_train_fraction",
            "selected": selected,
            "explanation_aggregation": explanation["aggregation"],
            "explanation_miner": "inductive",
        },
    )
    save_csv(folder / "candidates.csv", all_candidates)


if __name__ == "__main__":
    main()
