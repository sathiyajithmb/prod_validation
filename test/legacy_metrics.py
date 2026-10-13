"""Expected Legacy Operations display values derived from captured API responses."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import json
import re

NUMBER = r"\d[\d,]*(?:\.\d+)?"
GROUPS = (
    ("Salaried", "salaried", "S"),
    ("Full Time", "full_time", "FT"),
    ("Steady Eddy", "part_time_dedicated", "SE"),
    ("Part Time", "part_time", "PT"),
    ("Episodic", "episodic", "EP"),
)


def numeric(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing numeric API value")
    match = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)(?:\s+hrs)?", str(value).strip())
    if not match:
        raise ValueError("Invalid numeric API value")
    parsed = float(match[1].replace(",", ""))
    return int(parsed) if parsed.is_integer() else parsed


def label_pattern(label):
    return re.escape(label).replace(r"\ ", r"\s+")


def metric(card, label, value, unit="cases", pattern=None, note=""):
    return {
        "card": card, "label": label, "value": numeric(value), "unit": unit,
        "pattern": pattern or rf"(?:^|\n)\s*{label_pattern(label)}\s+__VALUE__",
        "note": note,
    }


def read_metric(text, item):
    pattern = item["pattern"].replace("__VALUE__", rf"(?P<value>{NUMBER})(?![\d,.])")
    match = re.search(pattern, text)
    return numeric(match["value"]) if match else None


def expected_pattern(item):
    value = item["value"]
    expected = rf"(?:{re.escape(str(value))}|{re.escape(format(value, ','))})(?![\d,.])"
    return re.compile(item["pattern"].replace("__VALUE__", expected))


def build_metrics(payloads, endpoints, today):
    data = [payloads[path] for path in endpoints]
    metrics = []

    def typed(index):
        rows = data[index]
        if not isinstance(rows, list):
            raise ValueError("Expected an aggregate row list")
        values = {}
        for row in rows:
            key = row["type"]
            if key in values:
                raise ValueError("Duplicate aggregate type")
            values[key] = row
        return values

    def add_rows(card, index, mapping):
        rows = typed(index)
        for label, api_label in mapping:
            metrics.append(metric(card, label, rows[api_label]["count"]))
        return rows

    pending = add_rows("Cases Pending", 0, [
        ("RTA", "RTA"), ("Needs Attention", "Need Attention"), ("Posted", "Posted"),
        ("In Progress", "InProgress"), ("Overflow", "OverFlow"), ("On Hold", "OnHold"),
    ])
    for label, key in (("Posted", "Posted"), ("In Progress", "InProgress")):
        metrics.append(metric("Cases Pending", label + " - timeout",
            pending[key]["timeout_count"],
            pattern=rf"(?:^|\n)\s*{label_pattern(label)}\s+{NUMBER}\s*\(\s*__VALUE__\s*\)"))

    add_rows("Cases Completed", 1, [(x, x) for x in
        ("Yesterday", "Today", "Last Week", "This Week", "Last Month", "This Month")])
    over = typed(2)
    for state in ("Pending", "Completed"):
        for period in ("Weekday", "Weekend"):
            metrics.append(metric("Cases Past 24 Hrs", f"{state} {period}",
                over[state][period.lower() + "_case_count"]))
    add_rows("Exceptions Pending", 3, [
        ("STAT", "STAT"), ("Expedited", "Expedited"), ("Needs More Info", "Need More Info"),
        ("Time Out", "Time Out"), ("Decline", "Decline")])
    additions = add_rows("Addendums & Critical", 4, [(x, x) for x in
        ("Addendums Pending", "CF Pending", "Addendums Completed", "CF Completed")])
    metrics.append(metric("Addendums & Critical", "CF Pending - timeout",
        additions["CF Pending"]["timeout_count"],
        pattern=rf"(?:^|\n)\s*CF\s+Pending\s+{NUMBER}\s*\(\s*__VALUE__\s*\)"))
    tat = typed(5)
    for label in ("Weekday", "Weekend", "Overall"):
        metrics.append(metric("Average TAT", label, tat[label]["count"], "hours",
            pattern=rf"(?:^|\n)\s*{label}\s+__VALUE__\s+hrs\b",
            note="Compared with the API display count (rounded hours)."))
    for label, row in typed(6).items():
        metrics.append(metric("Rooms", label, row["count"]))

    capacity = data[7]
    if capacity.get("response", {}).get("error", 0) != 0:
        raise ValueError("Capacity API reported an error")
    radiologists = capacity["content"]
    if not isinstance(radiologists, list):
        raise ValueError("Invalid capacity data")
    lookup = {str(row["rad_id"]): row for row in radiologists}
    out = {group: 0 for _, group, _ in GROUPS}
    for row in radiologists:
        schedule = next((x for x in row["capacity_details"] if x["date"] == today), None)
        if schedule is not None and row["work_type"] in out:
            # Same availability rule used by the dashboard: capacity <= 0.
            raw = str(schedule["capacity"])
            parsed = re.match(r"^[+-]?\d+", raw)
            available = parsed is not None and int(parsed[0]) > 0
            if not available:
                out[row["work_type"]] += 1
    for label, group, abbreviation in GROUPS:
        metrics.append(metric("Out Of Office", label, out[group], "radiologists"))
        metrics.append(metric("Load Balancing", "Out of office / " + label,
            out[group], "radiologists",
            pattern=rf"(?:^|\n)\s*{abbreviation}\s*\(\s*__VALUE__\s*\)"))

    chart_rows = data[8]
    if not isinstance(chart_rows, list) or len(chart_rows) != 1:
        raise ValueError("Unexpected load-balancing aggregate response")
    aggregate = chart_rows[0]["aggregated_data_json"]
    jobs = json.loads(aggregate) if aggregate else []
    if not isinstance(jobs, list):
        raise ValueError("Invalid load-balancing data")
    for direction in ("Under", "Over"):
        for column, (label, group, _) in enumerate(GROUPS):
            # Verified against the current dashboard bundle: its EP column
            # uses part_time_dedicated, just like SE. This tests displayed
            # behavior, not the business correctness of that classification.
            source_group = "part_time_dedicated" if group == "episodic" else group
            count = 0
            for row in jobs:
                work_type = lookup.get(str(row["radiologist_id"]), {}).get("work_type")
                remaining = numeric(row["current_total"]) - numeric(row["cases_completed"])
                if work_type == source_group and ((remaining > 0) == (direction == "Under")):
                    count += 1
            prefix = rf"(?:^|\n)\s*{direction}" + rf"(?:\s+{NUMBER})" * column
            note = "Matches current dashboard grouping and current_total minus cases_completed."
            if group == "episodic":
                note += " Dashboard EP currently uses the Steady Eddy work type."
            metrics.append(metric("Load Balancing", f"{direction} / {label}",
                count, "radiologists", pattern=prefix + r"\s+__VALUE__", note=note))
    return metrics
