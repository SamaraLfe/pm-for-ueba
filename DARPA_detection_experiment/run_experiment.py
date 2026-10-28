"""Последовательный запуск этапов эксперимента из текущей папки."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    steps = [
        "read_data.py", "aggregate.py", "tune_inductive.py", "train_models.py",
        "controls.py", "model_quality.py", "explain.py", "plot_results.py", "report.py",
    ]
    if args.download:
        steps.insert(0, "acquire.py")
    environment = {
        **os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for script in steps:
        print(f"Этап: {script}", flush=True)
        subprocess.run([sys.executable, str(ROOT/script)], env=environment, check=True)
    print(f"Готовый отчёт: {ROOT/'report.html'}", flush=True)


if __name__ == "__main__":
    main()
