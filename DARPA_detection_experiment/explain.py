"""Минимальное выравнивание реальных трасс и его отображение на исходной сети."""

import csv
import heapq
import itertools
import time
from collections import Counter
from pathlib import Path
import pm4py
from graphviz import Digraph
from pm4py.objects.petri_net import semantics
from io_utils import load_json, save_csv, save_json
from train_models import ROOT

RULE = "host_300s"
MINER = "inductive"


def shortest_alignment(actions, net, initial, final, timeout=15, max_states=100000):
    """Dijkstra: sync/tau стоят 0, log/model-only стоят 1. При лимите — unknown.

    Состояние — позиция в трассе и маркировка. Второй критерий минимизирует
    число шагов среди решений с одинаковой стоимостью отклонений.
    """
    places = sorted(net.places, key=lambda p: p.name)
    transitions = sorted(net.transitions, key=lambda t: (t.label or "", t.name))
    encode = lambda marking: tuple(marking.get(p, 0) for p in places)
    decode = lambda values: pm4py.objects.petri_net.obj.Marking(
        {p: n for p, n in zip(places, values) if n}
    )
    initial_state = (0, encode(initial))
    target = (len(actions), encode(final))
    best = {initial_state: (0, 0)}
    previous = {}
    serial = itertools.count()
    queue = [(0, 0, next(serial), initial_state)]
    deadline = time.monotonic() + timeout

    def offer(state, following, kind, transition, log_action, cost):
        edits, steps = best[state]
        value = (edits + cost, steps + 1)
        if following not in best or value < best[following]:
            best[following] = value
            previous[following] = (state, kind, transition, log_action)
            heapq.heappush(queue, (*value, next(serial), following))

    while queue:
        if time.monotonic() > deadline or len(best) > max_states:
            return {"status": "unknown", "visited_states": len(best), "moves": []}
        edits, steps, _, state = heapq.heappop(queue)
        if best[state] != (edits, steps):
            continue
        if state == target:
            path = []
            current = state
            while current != initial_state:
                prior, kind, transition, log_action = previous[current]
                path.append(
                    {
                        "kind": kind,
                        "transition": transition.name if transition else None,
                        "model_action": transition.label if transition else None,
                        "log_action": log_action,
                        "position": prior[0],
                        "before": {p.name: n for p, n in zip(places, prior[1]) if n},
                        "after": {p.name: n for p, n in zip(places, current[1]) if n},
                    }
                )
                current = prior
            path.reverse()
            return {
                "status": "complete",
                "cost": edits,
                "visited_states": len(best),
                "moves": path,
            }
        position, values = state
        marking = decode(values)
        if position < len(actions):
            offer(state, (position + 1, values), "log", None, actions[position], 1)
        for transition in transitions:
            if not semantics.is_enabled(transition, net, marking):
                continue
            following = semantics.execute(transition, net, marking)
            if transition.label is None:
                offer(state, (position, encode(following)), "tau", transition, None, 0)
            else:
                offer(state, (position, encode(following)), "model", transition, None, 1)
                if position < len(actions) and transition.label == actions[position]:
                    offer(
                        state,
                        (position + 1, encode(following)),
                        "sync",
                        transition,
                        actions[position],
                        0,
                    )
    return {"status": "no_path", "moves": [], "visited_states": len(best)}


def validate_alignment(actions, alignment, net, initial, final):
    """Независимо исполнить путь PM4Py-семантикой и проверить исходную трассу."""
    if alignment["status"] != "complete":
        return False
    transition_by_name = {t.name: t for t in net.transitions}
    marking = initial.copy()
    observed = []
    cost = 0
    for move in alignment["moves"]:
        assert {p.name: n for p, n in marking.items() if n} == move["before"]
        if move["kind"] in ("sync", "log"):
            observed.append(move["log_action"])
        if move["kind"] != "log":
            transition = transition_by_name[move["transition"]]
            assert semantics.is_enabled(transition, net, marking)
            marking = semantics.execute(transition, net, marking)
        cost += move["kind"] in ("log", "model")
        assert {p.name: n for p, n in marking.items() if n} == move["after"]
    assert observed == actions
    assert marking == final
    assert cost == alignment["cost"]
    return True


def render_alignment(net, initial, final, alignment, path):
    """Рисовать исходную сеть; log-only показывать отдельно, не добавляя в неё дуг."""
    graph = Digraph("alignment", graph_attr={"rankdir": "LR", "bgcolor": "white"})
    graph.attr("node", fontname="Arial", fontsize="11")
    graph.attr("edge", color="#8b9399", arrowsize="0.6")
    moves = alignment.get("moves", [])
    sync = {m["transition"] for m in moves if m["kind"] == "sync"}
    model = {m["transition"] for m in moves if m["kind"] == "model"}
    node_ids = {}
    for index, place in enumerate(sorted(net.places, key=lambda p: p.name)):
        node_ids[place] = f"p_{index}"
        label = str(initial.get(place, ""))
        graph.node(
            node_ids[place],
            label=label,
            shape="circle",
            width=".32",
            id="place_" + place.name,
            peripheries="2" if place in final else "1",
        )
    for index, transition in enumerate(sorted(net.transitions, key=lambda t: t.name)):
        node_ids[transition] = f"t_{index}"
        color = (
            "#d8eadc"
            if transition.name in sync
            else "#fff0cf" if transition.name in model else "#f1f2f3"
        )
        if transition.label is None:
            color = "#525a60"
        graph.node(
            node_ids[transition],
            label=transition.label or "τ",
            shape="box",
            style="filled",
            fillcolor=color,
            id="transition_" + transition.name,
            fontcolor="white" if transition.label is None else "#20252a",
        )
    for arc in sorted(net.arcs, key=lambda a: (node_ids[a.source], node_ids[a.target])):
        graph.edge(
            node_ids[arc.source],
            node_ids[arc.target],
            label=str(arc.weight) if arc.weight != 1 else "",
        )
    log_counts = Counter(m["log_action"] for m in moves if m["kind"] == "log")
    if log_counts:
        with graph.subgraph(name="cluster_log") as cluster:
            cluster.attr(
                label="События без синхронного перехода\n(вне исходной сети)",
                fontname="Arial",
                fontsize="11",
                color="#b44646",
                style="dashed",
            )
            for i, (label, count) in enumerate(sorted(log_counts.items())):
                cluster.node(
                    f"log_{i}",
                    label=f"{label} × {count}",
                    shape="box",
                    style="filled,dashed",
                    fillcolor="#fbe0e0",
                    color="#b44646",
                )
    path.write_text(graph.pipe(format="svg").decode("utf-8"), "utf-8")


def select_examples(rows):
    """Постфактум выбрать иллюстрации разных исходов, не меняя обученные модели."""
    selected = []
    definitions = [
        (
            "tp_novel",
            "Обнаруженная атака: новое действие",
            lambda r: r["actual_attack"]
            and r["predicted_attack"]
            and 0 < r["unknown_activities"] < r["steps"]
            and 3 <= r["steps"] <= 15,
        ),
        (
            "tp_known",
            "Обнаруженная атака: известные действия",
            lambda r: r["actual_attack"]
            and r["predicted_attack"]
            and r["unknown_activities"] == 0,
        ),
        (
            "fn_deviation",
            "Пропущенная атака: отклонение ниже порога",
            lambda r: r["actual_attack"]
            and not r["predicted_attack"]
            and r["exact_accept"] is False
            and r["unknown_activities"] == 0
            and 2 <= r["steps"] <= 15,
        ),
        (
            "fn_fitting",
            "Пропущенная атака: допустимое поведение модели",
            lambda r: r["actual_attack"]
            and not r["predicted_attack"]
            and r["exact_accept"] is True
            and 1 <= r["steps"] <= 15,
        ),
        (
            "false_positive",
            "Срабатывание на фоновую трассу",
            lambda r: not r["actual_attack"]
            and r["predicted_attack"]
            and 2 <= r["steps"] <= 15,
        ),
        (
            "true_negative",
            "Фоновая трасса без срабатывания",
            lambda r: not r["actual_attack"]
            and not r["predicted_attack"]
            and r["exact_accept"] is True
            and 3 <= r["steps"] <= 8,
        ),
    ]
    for example_id, title, predicate in definitions:
        eligible = [r for r in rows if predicate(r)]
        if eligible:
            chosen = min(eligible, key=lambda r: (r["steps"], r["case_id"]))
            selected.append({"example_id": example_id, "title": title, **chosen})
    return selected


def describe(example, alignment):
    """Отделить объяснение правила детектора от содержательного вывода об атаке."""
    sign = "превышает" if example["predicted_attack"] else "не превышает"
    text = f"Score {example['score']:.4f} {sign} порог {example['threshold']:.4f}. "
    if alignment["status"] != "complete":
        return (
            text
            + "Точное выравнивание не завершено в заданных пределах; путь не интерпретируется."
        )
    counts = Counter(m["kind"] for m in alignment["moves"])
    text += (
        f"В минимальном выравнивании: синхронных шагов — {counts['sync']}; "
        f"шагов только журнала — {counts['log']}; видимых шагов только модели — {counts['model']}. "
    )
    if example["example_id"] == "fn_fitting":
        text += "Размеченный атакующий эпизод допускается сетью без видимых исправлений. Данное представление процесса не отделяет его от допустимого поведения."
    elif example["example_id"] == "fn_deviation":
        text += "Структурное расхождение существует, однако итоговая оценка ниже порога. Это объясняет пропуск детектора, но не отменяет атаку в эталонной разметке."
    elif example["example_id"] == "false_positive":
        text += "Сеть описывает фон неполно: процессное расхождение приводит к срабатыванию на объект вне опубликованной разметки атаки. Безвредность этого объекта независимо не установлена."
    elif example["unknown_activities"]:
        text += "Часть score обусловлена отсутствием типов действий в модели. Красные события вне сети не являются обнаруженными новыми переходами этой сети."
    else:
        text += "Результат связан с соответствием наблюдаемой последовательности допустимому процессу. Сам по себе путь не доказывает причинность атаки."
    return text


def main():
    global RULE
    selection = load_json(ROOT / "results/tuning/selection.json")
    RULE = selection["explanation_aggregation"]
    folder = ROOT / "results" / RULE / MINER
    net, initial, final = pm4py.read_pnml(str(folder / "model.pnml"))
    with (folder / "trace_diagnostics.csv").open(encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    test = []
    for row in rows:
        if row["split"] != "test":
            continue
        for key in ("actual_attack", "predicted_attack"):
            row[key] = row[key] == "True"
        for key in ("steps", "unknown_activities", "events", "attack_events"):
            row[key] = int(row[key])
        for key in ("score", "threshold", "token_fitness"):
            row[key] = float(row[key])
        row["exact_accept"] = {"True": True, "False": False, "": None}[row["exact_accept"]]
        test.append(row)
    cases = {
        c["case_id"]: c for c in load_json(ROOT / "data/prepared" / RULE / "cases.json")
    }
    with (ROOT / "data/prepared" / RULE / "membership.csv").open(
        encoding="utf-8-sig"
    ) as source:
        members = list(csv.DictReader(source))
    explanations = []
    for example in select_examples(test):
        case = cases[example["case_id"]]
        alignment = shortest_alignment(case["actions"], net, initial, final)
        verified = validate_alignment(case["actions"], alignment, net, initial, final)
        target = ROOT / "results/explanations" / example["example_id"]
        target.mkdir(parents=True, exist_ok=True)
        save_json(target / "alignment.json", alignment)
        save_csv(
            target / "membership.csv",
            [r for r in members if r["case_id"] == case["case_id"]],
        )
        render_alignment(net, initial, final, alignment, target / "network.svg")
        explanation = {
            **example,
            "aggregation": RULE,
            "miner": MINER,
            "event_ids": case["event_ids"],
            "start_us": case["start_us"],
            "end_us": case["end_us"],
            "alignment": alignment,
            "alignment_verified": verified,
            "description": describe(example, alignment),
        }
        explanations.append(explanation)
        print(example["example_id"], alignment["status"], alignment.get("cost"), flush=True)
    save_json(ROOT / "results/explanations/summary.json", explanations)


if __name__ == "__main__":
    main()
