"""Три определения case при неизменных событиях и метках действий."""

from collections import Counter, defaultdict
from pathlib import Path
from io_utils import load_json, partition, read_events, save_csv, save_json
from read_data import internal

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/prepared"
RULES = ("host_60s", "host_300s", "host_session_30s")


def project_to_entities(events):
    """Сохранить все события, включая внешнюю цель и широковещательный трафик."""
    projected = defaultdict(list)
    for event in events:
        hosts = {ip for ip in (event["src"], event["dst"]) if internal(ip)}
        if not hosts:
            hosts = {event["dst"]}
        for host in hosts:
            if event["src"] == event["dst"]:
                direction = "SELF"
            else:
                direction = "OUT" if host == event["src"] else "IN"
            activity = direction + "_" + event["service"]
            projected[host].append((event, activity))
    return projected


def add_event(case, event, activity, membership):
    """Свернуть соседний повтор, сохранив его принадлежность исходному шагу."""
    case["end_us"] = max(case["end_us"], event["end_us"])
    case["event_ids"].append(event["event_id"])
    case["raw_steps"] += 1
    case["packets"] += event["packets"]
    case["attack_events"] += bool(event["attack_packets"])
    if event["phases"]:
        case["phases"].update(event["phases"].split(";"))
    if not case["actions"] or case["actions"][-1] != activity:
        case["actions"].append(activity)
    membership.append(
        {
            "case_id": case["case_id"],
            "step": len(case["actions"]),
            "event_id": event["event_id"],
            "activity": activity,
        }
    )


def make_cases(projected, rule, source):
    """Правило case использует только узел и время, без attack-меток."""
    cases = {}
    membership = []
    for host, entity_events in sorted(projected.items()):
        entity_events.sort(key=lambda pair: (pair[0]["start_us"], pair[0]["event_id"]))
        session = 0
        session_start = None
        previous_end = None
        for event, activity in entity_events:
            if rule == "host_session_30s":
                new_session = (
                    session_start is None
                    or event["start_us"] - previous_end > 30_000_000
                    or event["start_us"] - session_start >= 300_000_000
                )
                if new_session:
                    session += 1
                    session_start = event["start_us"]
                bucket = session
                previous_end = max(previous_end or event["end_us"], event["end_us"])
            else:
                seconds = 60 if rule == "host_60s" else 300
                bucket = event["start_us"] // (seconds * 1_000_000)
            case_id = f"{host}|{bucket}"
            case = cases.setdefault(
                case_id,
                {
                    "case_id": case_id,
                    "host": host,
                    "start_us": event["start_us"],
                    "end_us": event["end_us"],
                    "event_ids": [],
                    "actions": [],
                    "attack_events": 0,
                    "phases": set(),
                    "raw_steps": 0,
                    "packets": 0,
                },
            )
            add_event(case, event, activity, membership)
    rows = list(cases.values())
    for case in rows:
        case["split"] = partition(
            case["start_us"],
            case["end_us"],
            source["train_end_us"],
            source["calibration_end_us"],
        )
        case["reference_label"] = (
            "attack" if case["attack_events"] else "background_reference"
        )
        case["phases"] = sorted(case["phases"])
    return rows, membership


def main():
    source = load_json(OUT / "source_summary.json")
    events = read_events(OUT / "events.csv")
    projected = project_to_entities(events)
    statistics = []
    for rule in RULES:
        cases, membership = make_cases(projected, rule, source)
        folder = OUT / rule
        save_json(folder / "cases.json", cases)
        save_csv(folder / "membership.csv", membership)
        assert {row["event_id"] for row in membership} == {e["event_id"] for e in events}
        statistics.append(
            {
                "rule": rule,
                "cases": len(cases),
                "membership": len(membership),
                "singletons": sum(len(c["actions"]) == 1 for c in cases),
                "splits": dict(Counter(c["split"] for c in cases)),
                "test_labels": dict(
                    Counter(c["reference_label"] for c in cases if c["split"] == "test")
                ),
            }
        )
        print(f"{rule}: {len(cases)} трасс", flush=True)
    source.update(
        {
            "entity_anchors": len(projected),
            "internal_hosts": sum(internal(host) for host in projected),
            "aggregations": statistics,
        }
    )
    save_json(OUT / "summary.json", source)


if __name__ == "__main__":
    main()
