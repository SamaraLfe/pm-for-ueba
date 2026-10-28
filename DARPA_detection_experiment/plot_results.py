"""Один научный рисунок сравнения, без утверждения независимости трасс."""

import json
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

ROOT = Path(__file__).resolve().parent


def main():
    results = [
        r
        for r in json.loads((ROOT / "results/summary.json").read_text("utf-8"))
        if r["status"] == "complete"
    ]
    names = {"host_60s": "1 мин", "host_300s": "5 мин", "host_session_30s": "Сеанс"}
    miners = {"alpha_plus": "Alpha+", "inductive": "Inductive Miner"}
    colors = {"alpha_plus": "#666666", "inductive": "#33658a"}
    fig, axes = plt.subplots(1, 3, figsize=(15, 6), sharey=True, layout="constrained")
    labels = [names[r["rule"]] + " · " + miners[r["miner"]] for r in results]
    for ax, key, title in zip(
        axes,
        ("precision", "recall", "fpr"),
        ("Precision", "Recall", "Ложные срабатывания (FPR)"),
    ):
        values = [r["test"][key] or 0 for r in results]
        ax.barh(
            range(len(results)),
            values,
            color=[colors[r["miner"]] for r in results],
            height=0.62,
        )
        ax.set_title(title)
        ax.set_xlim(0, 1 if key != "fpr" else max(0.02, max(values) * 1.25))
        ax.xaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(axis="x", alpha=0.25)
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        for i, value in enumerate(values):
            ax.text(
                value + 0.006 if key != "fpr" else value + 0.001,
                i,
                f"{100 * value:.1f}%",
                va="center",
                fontsize=9,
            )
    axes[0].set_yticks(range(len(labels)), labels)
    axes[0].invert_yaxis()
    fig.suptitle("DARPA LLDOS 1.0: разные агрегации и процессные модели", fontsize=15)
    fig.savefig(ROOT / "results/comparison.png", dpi=160)
    fig.savefig(ROOT / "results/comparison.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
