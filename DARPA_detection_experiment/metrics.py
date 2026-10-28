"""Метрики детекции и соответствия процессной модели."""

import time
from collections import Counter, defaultdict


def auc_metrics(rows):
    """AUROC и average precision с корректной обработкой одинаковых scores."""
    positives = sum((r["actual_attack"] for r in rows))
    negatives = len(rows) - positives
    if not positives or not negatives:
        return (None, None)
    groups = defaultdict(lambda: [0, 0])
    for r in rows:
        groups[r["score"]][0 if r["actual_attack"] else 1] += 1
    wins = 0.0
    lower_negative = 0
    for score, (p, n) in sorted(groups.items()):
        wins += p * (lower_negative + 0.5 * n)
        lower_negative += n
    ap = 0.0
    tp = 0
    seen = 0
    for score, (p, n) in sorted(groups.items(), reverse=True):
        tp += p
        seen += p + n
        ap += p / positives * (tp / seen)
    return (wins / (positives * negatives), ap)


def metrics(rows):
    """Рассчитать метрики классификации и соответствия; неизвестные значения не заменять нулём."""
    counts = Counter(
        (
            (
                ("TP" if r["actual_attack"] else "FP")
                if r["predicted_attack"]
                else "FN" if r["actual_attack"] else "TN"
            )
            for r in rows
        )
    )
    tp, fp, fn, tn = [counts[k] for k in ("TP", "FP", "FN", "TN")]
    divide = lambda a, b: a / b if b else None
    auc, ap = auc_metrics(rows)
    recall = divide(tp, tp + fn)
    specificity = divide(tn, tn + fp)
    balanced = (
        (recall + specificity) / 2
        if recall is not None and specificity is not None
        else None
    )
    return {
        "cases": len(rows),
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "accuracy": divide(tp + tn, len(rows)),
        "balanced_accuracy": balanced,
        "precision": divide(tp, tp + fp),
        "recall": recall,
        "fpr": divide(fp, fp + tn),
        "f1": divide(2 * tp, 2 * tp + fp + fn),
        "auroc": auc,
        "average_precision": ap,
        "mean_fitness": sum(r["token_fitness"] for r in rows) / len(rows) if rows else None,
        "strict_accepted": sum(r["exact_accept"] is True for r in rows),
        "strict_rejected": sum(r["exact_accept"] is False for r in rows),
        "strict_unknown": sum(r["exact_accept"] is None for r in rows),
    }
