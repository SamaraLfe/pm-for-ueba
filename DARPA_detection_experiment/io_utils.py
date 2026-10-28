"""Простые операции ввода-вывода; числовые типы восстанавливаются явно."""

import csv
import json


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), "utf-8"
    )


def load_json(path):
    return json.loads(path.read_text("utf-8"))


def save_csv(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_events(path):
    numeric = {"start_us", "end_us", "sport", "dport", "packets", "bytes", "attack_packets"}
    with path.open(encoding="utf-8-sig") as source:
        events = list(csv.DictReader(source))
    for event in events:
        for field in numeric:
            event[field] = int(event[field])
    return events


def partition(start, end, train_end, calibration_end):
    """Не включать пересекающий границу объект сразу в два раздела."""
    if end < train_end:
        return "train"
    if start >= train_end and end < calibration_end:
        return "calibration"
    if start >= calibration_end:
        return "test"
    return "boundary"
