"""Академический HTML-отчёт с просмотром сетей и пошаговыми выравниваниями."""

import html
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from io_utils import load_json

ROOT = Path(__file__).resolve().parent
RULE_NAMES = {
    "host_60s": "Узел × 1 минута",
    "host_300s": "Узел × 5 минут",
    "host_session_30s": "Сеансы активности узла",
}
MINER_NAMES = {"alpha_plus": "Alpha+", "inductive": "Inductive Miner"}
MOVE_NAMES = {
    "sync": "Синхронный",
    "tau": "Скрытый",
    "log": "Только журнал",
    "model": "Только модель",
}


def esc(value):
    return html.escape(str(value))


def number(value, digits=3):
    return "—" if value is None else f"{value:.{digits}f}"


def percent(value):
    return "—" if value is None else f"{100 * value:.1f}%"


def date(timestamp):
    return datetime.fromtimestamp(
        timestamp / 1000000.0, timezone(timedelta(hours=-5))
    ).strftime("%d.%m.%Y %H:%M:%S")


def table(headers, rows):
    head = "".join((f"<th>{esc(h)}</th>" for h in headers))
    body = "".join(
        ("<tr>" + "".join((f"<td>{esc(v)}</td>" for v in row)) + "</tr>" for row in rows)
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def svg(path, prefix, annotated=False):
    from lxml import etree

    element = etree.fromstring(path.read_bytes())
    for item in element.iter():
        identity = item.get("id")
        if not identity:
            continue
        if annotated and identity.startswith("place_"):
            item.set("data-place", identity.removeprefix("place_"))
        if annotated and identity.startswith("transition_"):
            item.set("data-transition", identity.removeprefix("transition_"))
        item.set("id", prefix + "_" + identity)
    return etree.tostring(element, encoding="unicode")


def code_extract(filename, function_names):
    import ast

    text = (ROOT / filename).read_text("utf-8")
    tree = ast.parse(text)
    chunks = []
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in function_names:
            found.add(node.name)
            chunks.append("\n".join(text.splitlines()[node.lineno - 1 : node.end_lineno]))
    if found != function_names:
        raise ValueError(f"Функции не найдены в {filename}: {function_names - found}")
    return "<pre><code>" + esc("\n\n".join(chunks)) + "</code></pre>"


def stage_code(title, filename, functions):
    return (
        f"<details><summary>{esc(title)}</summary>"
        f'<p class="small">Исходный файл: <a href="{esc(filename)}">{esc(filename)}</a>.</p>'
        + code_extract(filename, functions)
        + "</details>"
    )


def tuning_description(selection):
    chosen = selection["selected"]
    body = "<h3>Выбор noise_threshold</h3><p>Кандидат допускается к выбору при среднем скорректированном fitness не ниже 0.95 на train и 0.90 на calibration. Скорректированное соответствие равно <code>f × (1 − u/n)</code>: отсутствующие в модели действия не считаются полностью соответствующими.</p><p>Среди допустимых кандидатов максимизируется гармоническое среднее:</p><p><code>Q = 3 / (1/F_train + 1/F_calibration + 1/P_train)</code></p><p>где P_train — ETConformance precision. При равенстве Q предпочтение отдаётся меньшему числу дуг, затем меньшему порогу шума.</p>"
    body += table(
        ["Агрегация", "noise_threshold", "F train", "F calibration", "ET precision", "Q"],
        [
            [
                RULE_NAMES[rule],
                number(c["noise_threshold"], 2),
                number(c["train_corrected_fitness"]),
                number(c["calibration_corrected_fitness"]),
                number(c["et_precision_train"]),
                number(c["quality"]),
            ]
            for rule, c in chosen.items()
        ],
    )
    body += '<p><a href="results/tuning/candidates.csv">Результаты всех 27 кандидатов</a> · <a href="results/tuning/selection.json">Правило выбора и параметры</a>.</p>'
    return body


def explanation_rationale(selection, models, examples):
    rule = selection["explanation_aggregation"]
    chosen = selection["selected"][rule]
    body = f"<h3>Обоснование выбора модели и агрегации</h3><p>Для {len(examples)} иллюстраций выбрана модель Inductive Miner с noise_threshold={chosen['noise_threshold']:.2f} и агрегацией «{esc(RULE_NAMES[rule])}». Выбор опирается на train и calibration.</p><p>Для объяснения последовательности необходимы достаточное соответствие фону, ограничивающая способность сети и многошаговые трассы. Поэтому для каждой агрегации рассчитан <code>E = Q × d</code>, где d — доля многошаговых трасс в train. </p>"
    body += table(
        ["Агрегация", "Q", "Многошаговые train", "E", "Дуги"],
        [
            [
                RULE_NAMES[r],
                number(c["quality"]),
                percent(c["multi_step_train_fraction"]),
                number(c["explanation_suitability"]),
                c["arcs"],
            ]
            for r, c in selection["selected"].items()
        ],
    )
    body += f"<p>Максимальное E={chosen['explanation_suitability']:.3f} получено для «{esc(RULE_NAMES[rule])}»: доля многошаговых трасс составляет {percent(chosen['multi_step_train_fraction'])}, скорректированный fitness на calibration — {chosen['calibration_corrected_fitness']:.3f}, ET precision — {chosen['et_precision_train']:.3f}.</p>"
    return body


def build_models(models):
    options = "".join(
        (
            f"""<option value="{i}">{esc(RULE_NAMES[m['rule']])} — {esc(MINER_NAMES[m['miner']])}</option>"""
            for i, m in enumerate(models)
        )
    )
    panels = []
    for i, model in enumerate(models):
        folder = ROOT / "results" / model["rule"] / model["miner"]
        content = svg(folder / "model.svg", f"model{i}")
        structure = model["structure"]
        caption = f"Места: {structure['places']}; видимые переходы: {structure['transitions'] - structure['hidden']}; скрытые: {structure['hidden']}; дуги: {structure['arcs']}. Fitness train: {number(model['train']['mean_fitness'])}; fitness test: {number(model['test']['mean_fitness'])}."
        if model["miner"] == "inductive":
            selection = load_json(ROOT / "results/tuning/selection.json")
            caption += f" noise_threshold={selection['selected'][model['rule']]['noise_threshold']:.2f}."
        panels.append(
            f"""<div class="model-panel" data-model="{i}" {('' if i == 0 else 'hidden')}><p>{esc(caption)}</p><div class="network"><div class="net-stage">{content}</div></div><p class="small"><a href="results/{model['rule']}/{model['miner']}/model.pnml">PNML</a> · <a href="results/{model['rule']}/{model['miner']}/trace_diagnostics.csv">Диагностика трасс</a></p></div>"""
        )
    return (
        f'<div class="toolbar"><label>Модель <select id="model-select">{options}</select></label><button onclick="zoomModel(25)">Увеличить</button><button onclick="zoomModel(-25)">Уменьшить</button><button onclick="zoomModel(0)">Вписать</button><button onclick="openModel()">Открыть крупно</button></div>'
        + "".join(panels)
    )


def section8_intro(selection, models, examples):
    chosen = selection["selected"][selection["explanation_aggregation"]]
    return (
        "<p>В этом разделе разобраны шесть проверочных трасс: обнаруженные атаки, пропуски "
        "и результаты на фоне. Для каждой трассы сопоставлены эталонная разметка, решение "
        "детектора и конкретные расхождения с сетью Петри.</p>"
        f'<p class="explanation-model">Единая модель разбора: <strong>Inductive Miner, '
        f'«{esc(RULE_NAMES[selection["explanation_aggregation"]])}»</strong>, '
        f'noise_threshold = {chosen["noise_threshold"]:.2f}. '
        f'Порог детектора: <strong>{number(examples[0]["threshold"], 4)}</strong>.</p>'
        '<details class="explanation-method"><summary>Почему выбрана эта модель и как выполнено сопоставление</summary>'
        + explanation_rationale(selection, models, examples)
        + "<p>Сопоставление называется выравниванием. Оно показывает, какие действия журнала "
        "можно выполнить в сети, какие остаются без соответствующего перехода и какие шаги "
        "модели требуются дополнительно. Поиск Дейкстры выполняется по парам «позиция в трассе, "
        "маркировка». Совпадение и скрытый переход имеют стоимость 0, шаг только журнала "
        "или видимый шаг только модели — 1. Выбирается минимальная стоимость, затем минимальное "
        "число шагов. Лимиты: 15 секунд и 100 000 состояний на трассу. "
        "</p></details>"
        "<h3>Как читать разбор</h3>"
        "<p>Выберите пример, сопоставьте его оценку с порогом, затем изучите выделенные "
        "расхождения. Выбор строки шага показывает состояние сети перед этим шагом.</p>"
        '<div class="alignment-key">'
        '<p><span class="key-dot sync-dot"></span><strong>Совпадение:</strong> действие журнала '
        "выполняется разрешённым переходом сети — зелёный.</p>"
        '<p><span class="key-dot log-dot"></span><strong>Только журнал:</strong> действие '
        "обрабатывается без перехода сети; токены не перемещаются — красный.</p>"
        '<p><span class="key-dot model-dot"></span><strong>Только модель:</strong> выполняется '
        "видимый переход без соответствующей записи журнала — жёлтый.</p>"
        "<p><strong>Скрытый шаг τ:</strong> внутренний переход без наблюдаемого действия. "
        "Числа в кружках — токены; синий контур выделяет выбранный переход.</p></div>"
    )


def example_interpretation(example):
    explanations = {
        "tp_novel": (
            "В трассе присутствует метка, отсутствующая среди переходов модели. "
            "Она увеличивает оценку отклонения. В выбранном выравнивании действия, "
            "которые не удалось сопоставить переходам, показаны как шаги только журнала. "
            "Разметка атаки и срабатывание детектора согласуются."
        ),
        "tp_known": (
            "Все метки действий известны модели, однако наблюдаемая последовательность "
            "не воспроизводится без расхождений. Снижение fitness приводит к срабатыванию. "
            "Этот пример иллюстрирует обнаружение без неизвестных меток; конкретные "
            "несовпадения перечислены в таблице шагов."
        ),
        "fn_deviation": (
            "Расхождения с сетью присутствуют, но оценка не превышает установленный порог. "
            "Поэтому детектор пропускает размеченную трассу атаки. Причина пропуска в этом "
            "примере связана с правилом принятия решения: ненулевое отклонение оказалось "
            "недостаточным для срабатывания."
        ),
        "fn_fitting": (
            "Трасса содержит размеченное событие атаки, но её последовательность полностью "
            "допускается сетью. Отклонение равно нулю. Используемые категории действий "
            "и структура модели не отделяют этот случай от допустимого поведения."
        ),
        "false_positive": (
            "Оценка превышает порог из-за расхождения последовательности с моделью. "
            "При этом трасса не содержит событий опубликованной разметки атаки. "
            "Результат учитывается как фоновое срабатывание: процессное отклонение "
            "само по себе не устанавливает наличие атаки."
        ),
        "true_negative": (
            "Фоновая трасса полностью воспроизводится сетью. Наблюдаемые действия "
            "сопоставлены переходам без видимых исправлений; оценка отклонения равна нулю. "
            "Детектор не срабатывает, что согласуется с эталонной разметкой."
        ),
    }
    return explanations[example["example_id"]]


def build_explanations(examples):
    short_titles = {
        "tp_novel": "Обнаружение: неизвестная метка",
        "tp_known": "Обнаружение: известные метки",
        "fn_deviation": "Пропуск: отклонение ниже порога",
        "fn_fitting": "Пропуск: допустимая последовательность",
        "false_positive": "Фон: срабатывание",
        "true_negative": "Фон: без срабатывания",
    }
    kinds = {
        "sync": ("Совпадение", "Действие выполнено в сети"),
        "log": ("Только журнал", "Действие без исполнения перехода"),
        "model": ("Только модель", "Переход без записи журнала"),
        "tau": ("Скрытый шаг", "Внутреннее перемещение токенов"),
    }
    options = "".join(
        f'<option value="{i}">{i + 1}. {esc(short_titles[e["example_id"]])}</option>'
        for i, e in enumerate(examples)
    )
    panels = []
    for i, example in enumerate(examples):
        moves = example["alignment"]["moves"]
        counts = Counter(m["kind"] for m in moves)
        deviations = counts["log"] + counts["model"]
        outcome = (
            ("TP", "Обнаруженная трасса атаки")
            if example["actual_attack"] and example["predicted_attack"]
            else (
                ("FN", "Пропущенная трасса атаки")
                if example["actual_attack"]
                else (
                    ("FP", "Фоновое срабатывание")
                    if example["predicted_attack"]
                    else ("TN", "Фоновая трасса без срабатывания")
                )
            )
        )
        relation = ">" if example["predicted_attack"] else "≤"
        rows = []
        for j, move in enumerate(moves):
            kind = move["kind"]
            title, meaning = kinds[kind]
            rows.append(
                f'<tr class="move-row {kind}-row" data-step="{j}" data-kind="{kind}" '
                f'tabindex="0" aria-label="Шаг {j+1}: {esc(title)}" '
                f'onclick="selectStep({i},{j})" '
                f"onkeydown=\"if(event.key==='Enter'||event.key===' '){{event.preventDefault();selectStep({i},{j})}}\">"
                f'<td>{j + 1}</td><td><span class="move-type {kind}-label">{title}</span>'
                f'<span class="move-meaning">{meaning}</span></td>'
                f'<td>{esc(move["log_action"] or "—")}</td>'
                f'<td>{esc(move["model_action"] or ("τ" if kind == "tau" else "—"))}</td></tr>'
            )
        sequence = " → ".join(example["actions"].split(" > "))
        observed = (
            f'<p class="sequence observed-sequence">{esc(sequence)}</p>'
            if example["steps"] <= 12
            else f'<p>Последовательность содержит {example["steps"]} шагов.</p>'
            f"<details><summary>Показать полную последовательность</summary>"
            f'<p class="sequence observed-sequence">{esc(sequence)}</p></details>'
        )
        diagram = svg(
            ROOT / "results/explanations" / example["example_id"] / "network.svg",
            f"exp{i}",
            True,
        )
        evidence = (
            f'Совпадений — {counts["sync"]}; шагов только журнала — {counts["log"]}; '
            f'видимых шагов только модели — {counts["model"]}.'
        )
        focus_note = (
            ""
            if deviations
            else "Расхождений нет. В таблице показаны совпадающие действия."
        )
        metadata = table(
            ["Характеристика", "Значение"],
            [
                ["Идентификатор трассы", example["case_id"]],
                ["Интервал", date(example["start_us"]) + " — " + date(example["end_us"])],
                [
                    "Исходные события / шаги после свёртки",
                    f'{example["events"]} / {example["steps"]}',
                ],
                ["Фазы разметки", example["phases"] or "нет"],
                [
                    "Token fitness / неизвестные шаги",
                    f'{number(example["token_fitness"],4)} / {example["unknown_activities"]}',
                ],
                ["Стоимость выравнивания", example["alignment"]["cost"]],
            ],
        )
        panels.append(
            f'<article class="explanation {"focus-deviations" if deviations else ""}" '
            f'data-example="{i}" {"" if i == 0 else "hidden"}>'
            f'<h3>{i+1}. {esc(short_titles[example["example_id"]])}</h3>'
            '<div class="case-facts">'
            f'<div><span>Эталонная разметка</span><strong>{"Трасса с атакой" if example["actual_attack"] else "Фоновая трасса"}</strong></div>'
            f'<div><span>Решение детектора</span><strong>{"Срабатывание" if example["predicted_attack"] else "Без срабатывания"} · {outcome[0]}</strong></div>'
            f'<div><span>Оценка s {relation} порога</span><strong>{number(example["score"],4)} {esc(relation)} {number(example["threshold"],4)}</strong></div>'
            "</div><h4>Наблюдаемая последовательность</h4>"
            + observed
            + f'<div class="case-interpretation"><h4>Интерпретация результата</h4><p>{example_interpretation(example)}</p></div>'
            + f'<h4>Расхождения и положение в сети</h4><p>{evidence}</p><p class="small">{focus_note}</p>'
            '<div class="case-workspace"><div class="case-network">'
            '<div class="step-status" aria-live="polite"></div>'
            '<div class="network-toolbar"><button onclick="zoomExample(25)">+</button>'
            '<button onclick="zoomExample(-25)">−</button><button onclick="zoomExample(0)">Вписать</button>'
            '<button onclick="openExample()">Открыть сеть крупно</button></div>'
            f'<div class="network"><div class="net-stage">{diagram}</div></div>'
            '</div><div class="case-steps"><h4>Пошаговое сопоставление журнала с моделью</h4>'
            "<p>Одна строка — один шаг выравнивания: сопоставление наблюдаемого действия "
            "с переходом сети.</p>"
            '<p class="small">Одинаковые метки в двух последних столбцах означают совпадение. '
            "Прочерк в столбце модели — действие журнала без перехода сети; прочерк в столбце "
            "журнала — переход модели без наблюдаемого действия. Строка τ обозначает внутренний "
            "переход. Выбор строки показывает маркировку перед соответствующим шагом.</p>"
            '<div class="scroll alignment-table"><table><thead><tr><th>Шаг выравнивания</th>'
            "<th>Тип шага</th><th>Действие в журнале</th><th>Переход модели</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div></div></div><details><summary>Характеристики трассы</summary>"
            + metadata
            + f'<p class="small"><a href="results/explanations/{example["example_id"]}/alignment.json">Выравнивание JSON</a> · '
            f'<a href="results/explanations/{example["example_id"]}/membership.csv">Связь шагов с исходными событиями</a></p></details></article>'
        )
    return (
        '<div class="example-picker"><label for="example-select">Пример для разбора</label>'
        f'<select id="example-select">{options}</select>'
        '<div class="step-filters"><label><input id="show-all-moves" type="checkbox"> Все видимые шаги</label>'
        '<label><input id="show-tau" type="checkbox"> Скрытые шаги τ</label></div></div>'
        + "".join(panels)
    )


def detection_definitions():
    return (
        "<h3>Интерпретация показателей</h3>"
        "<p>Единицей оценки является трасса. Положительный класс attack означает наличие "
        "хотя бы одного размеченного события атаки; background_reference — отсутствие "
        "такого события по использованной разметке.</p>"
        + table(
            ["Показатель", "Определение и интерпретация"],
            [
                ["TP", "Трассы attack, на которых детектор сработал."],
                ["FP", "Трассы background_reference, на которых детектор сработал."],
                ["FN", "Трассы attack без срабатывания детектора."],
                ["TN", "Трассы background_reference без срабатывания детектора."],
                ["Accuracy", "(TP + TN) / (TP + FP + FN + TN): доля правильных решений."],
                ["Recall", "TP / (TP + FN): доля обнаруженных трасс атаки."],
                ["Precision", "TP / (TP + FP): доля трасс attack среди срабатываний."],
                ["FPR", "FP / (FP + TN): доля фоновых трасс со срабатыванием."],
                [
                    "Balanced accuracy",
                    "(Recall + 1 − FPR) / 2: среднее качество распознавания двух классов с равными весами.",
                ],
                [
                    "F1",
                    "2 × Precision × Recall / (Precision + Recall): гармоническое среднее precision и recall.",
                ],
                [
                    "AUROC",
                    "Качество ранжирования по score: вероятность более высокой оценки у случайной трассы attack по сравнению с фоновой; равные оценки учитываются с весом 0.5. Значение 0.5 соответствует случайному ранжированию, 1 — полному разделению.",
                ],
                [
                    "AP",
                    "Average precision: сумма precision на последовательных порогах, взвешенная по приросту recall. Показывает концентрацию трасс attack в верхней части ранжированного списка; зависит от доли положительного класса.",
                ],
            ],
        )
    )


def control_results(models):
    return (
        "<h3>Интерпретация контрольных результатов</h3>"
        "<p>Во всех трёх агрегациях контроль меток вне train обеспечивает больший recall "
        "и меньший FPR, чем основное правило Inductive Miner. Контроли «вне модели» "
        "и «вне train» дают одинаковые результаты. Следовательно, новизна меток является "
        "основным выявленным признаком обнаружения; добавление fitness не улучшило эти "
        "показатели. Самостоятельный вклад порядка событий по результатам контрольных "
        "проверок не установлен.</p>"
    )


def alignment_legend():
    return (
        "<h3>Обозначения шагов выравнивания</h3>"
        "<p>Выравнивание сопоставляет наблюдаемые действия с исполнением сети. "
        "В таблице ниже указаны смысл каждого типа шага и его отображение на схеме.</p>"
        + table(
            ["Тип шага", "Определение", "Отображение / стоимость"],
            [
                [
                    "Синхронный",
                    "Очередное действие журнала совпадает с меткой разрешённого перехода. Обрабатывается действие и исполняется переход сети.",
                    "Зелёный / 0",
                ],
                [
                    "Видимый шаг только модели",
                    "Исполняется разрешённый переход с меткой действия, которому не сопоставлена запись журнала. Позиция в трассе не меняется; это гипотетический шаг сопоставления.",
                    "Жёлтый / 1",
                ],
                [
                    "Шаг только журнала",
                    "Обрабатывается действие журнала без исполнения перехода сети. Маркировка не меняется. Метка может отсутствовать в сети либо не соответствовать выбранному пути исполнения.",
                    "Красный, вне исходной сети / 1",
                ],
                [
                    "Скрытый шаг τ",
                    "Исполняется внутренний переход без наблюдаемой метки. Позиция в журнале не меняется.",
                    "Строки доступны по переключателю «Показать скрытые шаги» / 0",
                ],
            ],
        )
        + '<p class="legend">Синий контур выделяет переход выбранного шага, если этот шаг '
        "содержит переход модели. Числа в местах показывают количество токенов перед шагом — "
        "маркировку сети на выбранном пути выравнивания. Для шага только журнала соответствующего "
        "перехода в сети нет.</p>"
    )


def experimental_conclusions(models, source):
    im = [m for m in models if m["miner"] == "inductive"]
    alpha = [m for m in models if m["miner"] == "alpha_plus"]
    best = max(models, key=lambda m: m["test"]["f1"])
    return (
        "<p>На полном журнале Inside сценария DARPA 2000 LLDOS 1.0 установлена зависимость "
        "числа и состава трасс от правила агрегации. Минутные окна сформировали 3112 трасс, "
        "пятиминутные — 1078, сеансы активности — 2044. Число одношаговых трасс составило "
        "1895, 490 и 1235 соответственно. Изменение агрегации меняет единицу оценки, "
        "поэтому межвариантные различия метрик относятся к соответствующим наборам трасс.</p>"
        f"<p>Средний train fitness сетей Alpha+ находится в диапазоне "
        f'{min(m["train"]["mean_fitness"] for m in alpha):.3f}–'
        f'{max(m["train"]["mean_fitness"] for m in alpha):.3f}, '
        "что указывает на недостаточное воспроизведение обучающего фона. Для выбранных "
        f"Inductive Miner этот диапазон составляет "
        f'{min(m["train"]["mean_fitness"] for m in im):.3f}–'
        f'{max(m["train"]["mean_fitness"] for m in im):.3f}. '
        "По заданному критерию подбора noise_threshold выбраны значения 0.10 для минутных "
        "и пятиминутных окон и 0.15 для сеансов активности.</p>"
        f"<p>Среди шести основных вариантов наибольшая F1 получена для "
        f'{esc(MINER_NAMES[best["miner"]])} с агрегацией «{esc(RULE_NAMES[best["rule"]])}»: '
        f'F1 {percent(best["test"]["f1"])}, precision {percent(best["test"]["precision"])}, '
        f'recall {percent(best["test"]["recall"])} и FPR {percent(best["test"]["fpr"])}. '
        "Во всех агрегациях Inductive Miner обеспечивает больший recall, чем Alpha+, "
        "при более высокой доле фоновых срабатываний.</p>"
        "<p>Контроль наличия меток вне train обнаруживает 36 из 51 трассы attack для минутных "
        "окон, 37 из 48 для пятиминутных и 36 из 48 для сеансов. Соответствующий FPR составляет "
        "0.52%, 1.23% и 0.62%. Во всех трёх случаях этот контроль превосходит основное правило "
        "Inductive Miner одновременно по recall и FPR. Преимущество объединённой оценки "
        "процессного несоответствия над простым признаком новизны меток в данном эксперименте "
        "не подтверждено.</p>"
        "<p>Выравнивания выбранных test-трасс локализуют шаги только журнала и только модели. "
        "Разбор выявил как пропуск трассы с ненулевым отклонением ниже порога, так и пропуск "
        "размеченной трассы, полностью допускаемой сетью. Также зафиксировано отклонение "
        "на фоновой трассе, приводящее к срабатыванию. Следовательно, процессная допустимость "
        "и наличие метки атаки не совпадают в используемом представлении событий.</p>"
    )


def build_report():
    source = load_json(ROOT / "data/prepared/summary.json")
    models = [
        m for m in load_json(ROOT / "results/summary.json") if m["status"] == "complete"
    ]
    examples = load_json(ROOT / "results/explanations/summary.json")
    selection = load_json(ROOT / "results/tuning/selection.json")
    sections = []

    def section(identifier, title, body):
        if identifier == "detection":
            score_description, separator, results = body.partition("</p>")
            if not separator:
                raise ValueError("Описание score не найдено")
            threshold_description = (
                "<p>Порог определяется отдельно для каждой пары «агрегация — модель» "
                "по оценкам calibration. При используемом объёме калибровочных выборок "
                "эмпирический 99-й процентиль совпадает с максимальной оценкой "
                "<code>s</code> на calibration. Срабатывание на проверочной трассе "
                "регистрируется при строгом превышении этого порога.</p>"
            )
            body = (
                score_description
                + separator
                + threshold_description
                + detection_definitions()
                + results
            )
        elif identifier == "controls":
            body += control_results(models)
        elif identifier == "conclusion":
            body = experimental_conclusions(models, source)
        implementations = {
            "data": [
                (
                    "Код чтения PCAP и извлечения полей пакетов",
                    "read_data.py",
                    {"packets", "decode"},
                ),
                (
                    "Код присоединения разметки",
                    "read_data.py",
                    {"fingerprint", "read_attack_labels"},
                ),
            ],
            "aggregation": [
                (
                    "Код группировки пакетов в события сетевого обмена",
                    "read_data.py",
                    {"add_packet"},
                ),
                (
                    "Код сопоставления событий узлам",
                    "aggregate.py",
                    {"project_to_entities"},
                ),
            ],
            "protocol": [
                (
                    "Код разделения на train, calibration и test",
                    "io_utils.py",
                    {"partition"},
                ),
            ],
            "quality": [
                (
                    "Код обучения Alpha+ и Inductive Miner",
                    "train_models.py",
                    {"log_for", "discover_model"},
                ),
                (
                    "Код оценки кандидатов noise_threshold",
                    "tune_inductive.py",
                    {"corrected_fitness", "harmonic", "worker"},
                ),
                ("Код оценки precision процессной модели", "model_quality.py", {"worker"}),
            ],
            "detection": [
                (
                    "Код conformance checking, оценки аномальности и порога",
                    "train_models.py",
                    {"worker"},
                ),
                (
                    "Код точной проверки исполнения трассы",
                    "conformance.py",
                    {"exact_accept"},
                ),
                ("Код расчёта метрик детекции", "metrics.py", {"auc_metrics", "metrics"}),
            ],
            "controls": [
                ("Код контрольных оценок", "controls.py", {"main"}),
            ],
            "explanations": [
                (
                    "Код выбора модели и агрегации для объяснений",
                    "tune_inductive.py",
                    {"main"},
                ),
                (
                    "Код построения и проверки выравнивания",
                    "explain.py",
                    {"shortest_alignment", "validate_alignment"},
                ),
            ],
            "code": [
                (
                    "Код последовательного запуска эксперимента",
                    "run_experiment.py",
                    {"main"},
                ),
            ],
        }
        for label, filename, functions in implementations.get(identifier, []):
            body += stage_code(label, filename, functions)
        sections.append(f'<section id="{identifier}"><h2>{title}</h2>{body}</section>')

    section(
        "aim",
        "1. Цель и постановка эксперимента",
        "<p>Цель — установить, как правило формирования трасс влияет на качество процессной модели и обнаружение эпизодов известной атаки. Рассматривается LLDOS 1.0 DARPA 2000. Сравниваются Alpha+ и Inductive Miner.</p><p>Проверяются три вопроса: насколько сеть воспроизводит наблюдённый фон; позволяет ли несоответствие сети различать фон и размеченные атаки; какие конкретные расхождения можно локализовать на сети Петри.</p>",
    )
    overview = table(
        ["Характеристика", "Значение"],
        [
            ["Источник", "DARPA 2000, LLDOS 1.0"],
            ["Наблюдение", date(source["minimum_us"]) + " — " + date(source["maximum_us"])],
            ["События сетевого обмена после группировки пакетов", source["flows"]],
            [
                "Опорные адреса / внутренние узлы",
                f"{source['entity_anchors']} / {source['internal_hosts']}",
            ],
            [
                "События с attack / с background_reference",
                f"{source['flow_labels']['attack']} / {source['flow_labels']['background_reference']}",
            ],
        ],
    )
    data_description = (
        "<p>Используется полный файл сетевого трафика Inside сценария LLDOS 1.0 "
        "набора DARPA 2000: <code>inside_full.dump.gz</code>. Исходные данные представлены "
        "записью пакетов в формате PCAP. Запись каждого пакета содержит временную метку "
        "и байты сетевого кадра. Из кадров извлекаются IP-адреса отправителя и получателя, "
        "протокол TCP, UDP или ICMP, а для TCP и UDP — номера портов. "
        "Наблюдение охватывает фоновую сетевую активность и эпизоды известной атаки.</p>"
        "<p>Дополнительно используются пять файлов <code>phase-1-dump</code> — "
        "<code>phase-5-dump</code>, содержащих пакеты отдельных фаз атаки. "
        "Пакеты этих файлов сопоставляются с полной записью по временной метке и байтам кадра. "
        "Признак <code>attack</code> присваивается событию при наличии в нём размеченного "
        "пакета и трассе при наличии такого события. Обозначение "
        "<code>background_reference</code> соответствует отсутствию совпадения с этой разметкой.</p>"
        "<p>Журнал для Process Mining формируется при подготовке данных. Пакеты объединяются "
        "в события сетевого обмена по правилу раздела 3. Одно такое событие представляет "
        "группу пакетов между двумя конечными точками за ограниченный интервал времени. "
        "Для события сохраняются начало и конец, адреса, протокол, порты, число пакетов "
        "и объём данных. Указанное в таблице число событий относится к результату этой "
        "группировки. Затем события объединяются в трассы выбранным методом агрегации.</p>"
    )
    section("data", "2. Исходные данные и эталонная разметка", data_description + overview)
    section(
        "aggregation",
        "3. Построение событий и агрегация",
        "<p>Подготовка журнала включает объединение пакетов в события сетевого обмена, сопоставление событий сетевым узлам и формирование трасс. Пакеты TCP и UDP группируются по транспортному протоколу и неупорядоченной паре конечных точек IP:port. Для ICMP используются адреса и идентификатор echo-сообщения либо тип сообщения. Новое событие формируется, если интервал с предыдущим пакетом превышает 60 с или время от начала группы достигает 300 с. Сохраняются временные границы, количество и суммарный объём пакетов.</p>"
        + table(
            ["Метод", "Ключ case", "Границы"],
            [
                [
                    "Фиксированное окно 1 мин",
                    "узел + floor(t / 60 с)",
                    "границы Unix-времени",
                ],
                [
                    "Фиксированное окно 5 мин",
                    "узел + floor(t / 300 с)",
                    "границы Unix-времени",
                ],
                [
                    "Сеанс активности",
                    "узел + номер сеанса",
                    "пауза >30 с либо начало очередного события сетевого обмена ≥300 с от начала сеанса",
                ],
            ],
        )
        + "<p>Действие состоит из направления IN/OUT/SELF и категории: TCP WEB, MAIL, REMOTE, FILE, RPC, OTHER; UDP DNS, RPC, OTHER; ICMP.</p><details><summary>Код агрегации</summary>"
        + code_extract("aggregate.py", {"make_cases", "add_event"})
        + "</details>",
    )
    partition_table = table(
        [
            "Агрегация",
            "Всего",
            "Train",
            "Calibration",
            "Test",
            "Boundary",
            "Attack test",
            "Фон test",
            "Одношаговые",
        ],
        [
            [
                RULE_NAMES[a["rule"]],
                a["cases"],
                a["splits"]["train"],
                a["splits"]["calibration"],
                a["splits"]["test"],
                a["splits"]["boundary"],
                a["test_labels"]["attack"],
                a["test_labels"]["background_reference"],
                a["singletons"],
            ]
            for a in source["aggregations"]
        ],
    )
    section(
        "protocol",
        "4. Протокол обучения и проверки",
        "<p>Первые 60% интервала до первого размеченного пакета отведены обучению, следующие 20% — калибровке. Проверочная выборка начинается с оставшихся 20% предатакового интервала и продолжается до конца. Модели строятся только на train, пороги — только на calibration.</p>"
        + table(
            ["Граница", "Время"],
            [
                ["Конец train", date(source["train_end_us"])],
                ["Начало test", date(source["calibration_end_us"])],
                ["Первый размеченный пакет", date(source["first_attack_us"])],
            ],
        )
        + partition_table
        + "<p>Пересекающие границу cases сохраняются как boundary и не участвуют в метриках. Пересечение исходных event_id между train, calibration и test отсутствует. При разных агрегациях меняются единица оценки и число положительных объектов; прямое сравнение процентов требует учёта этих различий.</p>",
    )
    quality_rows = []
    for model in models:
        q = model.get("model_quality", {})
        train = model["train"]
        test = model["test"]
        exact = f"{train['strict_accepted']}/{train['cases']}"
        if train["strict_unknown"]:
            exact += f"; unknown={train['strict_unknown']}"
        quality_rows.append(
            [
                RULE_NAMES[model["rule"]],
                MINER_NAMES[model["miner"]],
                number(train["mean_fitness"]),
                exact,
                number(test["mean_fitness"]),
                number(q.get("etconformance_precision_train")),
                f"{test['strict_accepted']}/{test['cases']}",
                f"{model['structure']['places']} / {model['structure']['transitions']} / {model['structure']['arcs']}",
            ]
        )
    section(
        "quality",
        "5. Обучение и качество процессных моделей",
        "<p>Используется PM4Py 2.7.23.8. Alpha+ вызывается как ALPHA_VERSION_PLUS. Inductive Miner представлен одной моделью для каждой агрегации. Значение noise_threshold выбирается по train и calibration из сетки [0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50].</p><p>Fitness характеризует воспроизведение журнала. ETConformance precision оценивает допуск дополнительного поведения по префиксам train и является отдельной характеристикой модели. Для сетей с train fitness ниже 0.95 эта оценка не интерпретируется. Использована реализация PM4Py ETCONFORMANCE_TOKEN, поэтому значение зависит от поведения replay.</p>"
        + tuning_description(selection)
        + table(
            [
                "Агрегация",
                "Модель",
                "Fitness train",
                "Точно train",
                "Fitness test",
                "ET precision train",
                "Точно test",
                "P / T / A",
            ],
            quality_rows,
        )
        + "<p>Низкий fitness Alpha+ на train указывает на недостаточность полученной сети как модели фона.</p>"
        + build_models(models),
    )
    section(
        "detection",
        "6. Правило детекции и результаты",
        "<p>Основная оценка определена как <code>s = 1 − f × (1 − u / n)</code>, где f — token fitness, u — число шагов с метками вне модели, n — длина свёрнутой трассы. Штраф за неизвестные метки введён явно, поскольку числовой token fitness не всегда учитывает их.</p>"
        + table(
            [
                "Агрегация",
                "Модель",
                "Порог",
                "TP",
                "FP",
                "FN",
                "TN",
                "Accuracy",
                "Bal. accuracy",
                "Precision",
                "Recall",
                "FPR",
                "F1",
                "AUROC",
                "AP",
            ],
            [
                [
                    RULE_NAMES[m["rule"]],
                    MINER_NAMES[m["miner"]],
                    number(m["threshold"], 4),
                    *[m["test"][k] for k in ("TP", "FP", "FN", "TN")],
                    *[
                        percent(m["test"].get(k))
                        for k in (
                            "accuracy",
                            "balanced_accuracy",
                            "precision",
                            "recall",
                            "fpr",
                            "f1",
                        )
                    ],
                    number(m["test"]["auroc"]),
                    number(m["test"]["average_precision"]),
                ]
                for m in models
            ],
        )
        + '<figure><img src="results/comparison.png" alt="Сравнение результатов шести моделей"><figcaption>Рисунок 1. Precision, recall и FPR при порогах, заданных на calibration. Доверительные интервалы не приведены, поскольку трассы одного сценария не являются независимыми повторениями.</figcaption></figure>'
        + table(
            ["Агрегация", "Модель", "Фаза 1", "Фаза 2", "Фаза 3", "Фаза 4", "Фаза 5"],
            [
                [
                    RULE_NAMES[m["rule"]],
                    MINER_NAMES[m["miner"]],
                    *[
                        f"{m['per_phase'][str(p)]['detected_cases']}/{m['per_phase'][str(p)]['attack_cases']}"
                        for p in range(1, 6)
                    ],
                ]
                for m in models
            ],
        )
        + "<p>Для каждой фазы показано выявлено / число содержащих её cases. Один case может включать несколько фаз, поэтому столбцы не суммируются.</p>",
    )
    section(
        "controls",
        "7. Контроль вклада новых действий",
        "<p>Дополнительно рассчитаны три score: только доля неизвестных модели действий; доля действий вне словаря train; только 1−fitness. Каждый контроль имеет собственный порог по calibration. Это позволяет проверить, связано ли обнаружение преимущественно с новыми действиями.</p>"
        + table(
            [
                "Агрегация",
                "Модель",
                "Основной: recall / FPR",
                "Вне модели: recall / FPR",
                "Вне train: recall / FPR",
                "1−fitness: recall / FPR",
            ],
            [
                [
                    RULE_NAMES[m["rule"]],
                    MINER_NAMES[m["miner"]],
                    f"{percent(m['test']['recall'])} / {percent(m['test']['fpr'])}",
                    *[
                        f"{percent(m['controls'][k]['recall'])} / {percent(m['controls'][k]['fpr'])}"
                        for k in ("unknown_only", "train_unknown", "token_only")
                    ],
                ]
                for m in models
            ],
        ),
    )
    section(
        "explanations",
        "8. Объяснение отдельных трасс через сеть Петри",
        section8_intro(selection, models, examples) + build_explanations(examples),
    )
    files = [
        (
            "tune_inductive.py",
            "Подбор noise_threshold и выбор модели для объяснений без test",
        ),
        (
            "read_data.py",
            "Чтение PCAP, присоединение разметки и построение событий сетевого обмена",
        ),
        ("aggregate.py", "Определение case, проекции на узлы и сохранение членства"),
        (
            "train_models.py",
            "Обучение сетей только на train и расчёт trace-level диагностики",
        ),
        ("conformance.py", "Точная проверка исполнения с явно заданными лимитами"),
        ("metrics.py", "Метрики классификации и обработка одинаковых scores"),
        ("model_quality.py", "Оценка ETConformance precision на train"),
        ("explain.py", "Минимальное выравнивание, проверка пути и аннотированные сети"),
        ("controls.py", "Контрольные score и пороги по calibration"),
        ("run_experiment.py", "Последовательный запуск всех этапов"),
    ]
    code_body = "<p>Полный конвейер запускается одной командой из итоговой папки:</p><pre><code>./run.ps1</code></pre>"
    code_body += table(["Файл", "Назначение"], files)
    code_body += (
        "<p>" + " · ".join((f'<a href="{name}">{name}</a>' for name, _ in files)) + "</p>"
    )
    for name, functions, title in [
        ("read_data.py", {"packets", "add_packet"}, "Чтение данных и формирование события"),
        ("train_models.py", {"discover_model"}, "Обучение моделей"),
        ("metrics.py", {"metrics"}, "Метрики"),
        ("explain.py", {"validate_alignment"}, "Проверка объяснения"),
    ]:
        code_body += (
            f"<details><summary>{title}</summary>{code_extract(name, functions)}</details>"
        )
    section("code", "9. Реализация и воспроизводимость", code_body)
    section(
        "conclusion",
        "10. Выводы",
        "<p>Агрегация существенно меняет длину трасс, долю одношаговых случаев и наблюдаемые метрики детекции. Полученные Alpha+ сети плохо воспроизводят даже train. Выбранные модели Inductive Miner сохраняют высокое соответствие фоновым данным; оценка precision позволяет дополнительно контролировать допуск дополнительного поведения.</p><p>При текущем представлении событий значительная часть детекции обусловлена новыми метками действий. Некоторые сетевые случаи атаки могут полностью соответствовать процессной модели; отклонения могут также возникать у фоновых объектов. Выравнивания локализуют эти ситуации и объясняют решение выбранного процессного правила, включая ложные срабатывания и пропуски.</p><p>Границы вывода: один сценарий и один сенсор, около 18 минут фонового обучения, малая calibration, зависимые проекции, неполная определённость эталонного фона, портовые категории и технический порядок равновременных событий. Свёртка повторов исключает объём из score. Идентичность пользователей отсутствует; рассматривается поведение сетевых сущностей. Объясняющая модель внешнего детектора и независимая проверка на другом сценарии в этот эксперимент не входят.</p>",
    )
    section(
        "references",
        "Источники и файлы результатов",
        '<ol><li><a href="https://www.ll.mit.edu/r-d/datasets/2000-darpa-intrusion-detection-scenario-specific-datasets">MIT Lincoln Laboratory. 2000 DARPA Intrusion Detection Scenario Specific Datasets.</a></li><li><a href="https://archive.ll.mit.edu/ideval/data/2000/LLS_DDOS_1.0.html">MIT. LLDOS 1.0: описание сценария, исходные captures и разметка.</a></li><li><a href="https://processintelligence.solutions/pm4py/features">PM4Py. Process discovery, conformance checking and model evaluation.</a></li></ol><p><a href="results/comparison.csv">Все метрики CSV</a> · <a href="results/controls.csv">Контроли CSV</a> · <a href="results/summary.json">Полные результаты JSON</a> · <a href="data/manifest.json">Источники и SHA256</a></p>',
    )
    toc = (
        '<nav aria-label="Содержание">'
        + "".join(
            (
                f'<a href="#{identifier}">{title}</a>'
                for identifier, title in [
                    ("aim", "Постановка"),
                    ("data", "Данные"),
                    ("aggregation", "Агрегация"),
                    ("protocol", "Протокол"),
                    ("quality", "Модели"),
                    ("detection", "Детекция"),
                    ("controls", "Контроли"),
                    ("explanations", "Объяснения"),
                    ("code", "Код"),
                    ("conclusion", "Выводы"),
                ]
            )
        )
        + "</nav>"
    )
    script_data = json.dumps(
        [{"moves": e["alignment"]["moves"]} for e in examples], ensure_ascii=False
    ).replace("</", "<\\/")
    document = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Агрегация сетевых событий и процессная диагностика аномалий</title><style>'
        + (ROOT / "report_style.css").read_text("utf-8")
        + "</style></head><body><header><h1>Агрегация сетевых событий и процессная диагностика аномалий</h1></header>"
        + toc
        + "<main>"
        + "".join(sections)
        + f'</main><dialog id="model-dialog"><button onclick="this.parentElement.close()">Закрыть</button><div></div></dialog><script>const explanationData={script_data};</script><script>'
        + (ROOT / "report_interactions.js").read_text("utf-8")
        + "</script></body></html>"
    )
    (ROOT / "report.html").write_text(document, "utf-8")
    print(f"Академический отчёт: {len(models)} моделей, {len(examples)} объяснений")


if __name__ == "__main__":
    build_report()
