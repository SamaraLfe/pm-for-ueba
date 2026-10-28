"""Отдельная оценка ограничивающей способности процессных моделей."""

import argparse
import os
import subprocess
import sys
import time
import pm4py
from pm4py.algo.evaluation.precision import algorithm as precision
from io_utils import load_json, save_json
from train_models import ROOT, log_for


def worker(rule, miner):
    folder = ROOT / "results" / rule / miner
    result = load_json(folder / "result.json")
    # Precision имеет смысл совместно с fitness. На не воспроизводящем train
    # Alpha+ её значение могло бы создавать ложное впечатление качества.
    if result["train"]["mean_fitness"] < 0.95:
        value = {"status": "not_interpreted", "reason": "train_fitness_below_0.95"}
    else:
        net, initial, final = pm4py.read_pnml(str(folder / "model.pnml"))
        cases = load_json(ROOT / "data/prepared" / rule / "cases.json")
        log = log_for(c["actions"] for c in cases if c["split"] == "train")
        started = time.monotonic()
        measured = precision.apply(
            log,
            net,
            initial,
            final,
            variant=precision.Variants.ETCONFORMANCE_TOKEN,
            parameters={"show_progress_bar": False},
        )
        value = {
            "status": "complete",
            "etconformance_precision_train": float(measured),
            "seconds": time.monotonic() - started,
            "method": "PM4Py ETCONFORMANCE_TOKEN; estimate based on replayed prefixes",
        }
    save_json(folder / "quality.json", value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=2)
    args = parser.parse_args()
    if args.worker:
        return worker(*args.worker)
    results = load_json(ROOT / "results/summary.json")
    for model in results:
        if model["status"] != "complete":
            continue
        folder = ROOT / "results" / model["rule"] / model["miner"]
        try:
            with (folder / "quality.log").open("w", encoding="utf-8") as output:
                run = subprocess.run(
                    [sys.executable, __file__, "--worker", model["rule"], model["miner"]],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=60,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            if run.returncode:
                raise RuntimeError(f"Worker exit: {run.returncode}")
            quality = load_json(folder / "quality.json")
        except Exception as error:
            quality = {"status": "unknown", "reason": str(error)}
            save_json(folder / "quality.json", quality)
        model["model_quality"] = quality
        save_json(folder / "result.json", model)
        print(model["rule"], model["miner"], quality, flush=True)
    save_json(ROOT / "results/summary.json", results)


if __name__ == "__main__":
    main()
