"""Extract business IDs referenced by ASAGO garak artifacts.

Scans ``**/*-garak.json`` under a run directory and collects every ID that
matches ``\\b(KL|ACC|MG|CUST)-?\\d{2,6}\\b`` from:

- user turns,
- scripted tool-role turn contents,
- assistant tool_call arguments,
- the detector_rubric (all strings, recursively).

For each ID, details are gathered from scripted tool results (role "tool",
JSON content) whose record mentions the ID.

Usage:
    python3 -I extract_asago_ids.py [--root RUN_DIR] [--out OUT_JSON]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "examples" / "klarna-support" / "asago-run4"
DEFAULT_OUT = Path(__file__).resolve().parent / "asago_seed_ids.json"

ID_RE = re.compile(r"\b(KL|ACC|MG|CUST)-?\d{2,6}\b")
TYPE_BY_PREFIX = {"KL": "order", "ACC": "account", "MG": "merchant_group", "CUST": "customer"}
DETAIL_KEYS = (
    "merchant",
    "amount",
    "status",
    "items",
    "item",
    "date",
    "customer",
    "customer_id",
    "currency",
)


def has_id(ident: str, text: str) -> bool:
    return re.search(rf"(?<![A-Z0-9]){re.escape(ident)}(?!\d)", text) is not None


def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from iter_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_strings(value)


def tool_call_arguments(turn: dict):
    calls = turn.get("tool_calls") or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            return [calls]
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function", call)
        args = fn.get("arguments", "") if isinstance(fn, dict) else ""
        if isinstance(args, str):
            yield args
        else:
            yield from iter_strings(args)


def tool_records(content: str, ids_in_text: set[str]):
    """Yield dict records from a tool result that mention one of ids_in_text."""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return
    stack = [parsed]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            blob = json.dumps(node)
            if any(has_id(ident, blob) for ident in ids_in_text):
                yield node
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)


def full_id(match: str) -> str:
    prefix = match.split("-", 1)[0] if "-" in match else re.match(r"[A-Z]+", match).group(0)
    digits = re.search(r"\d+", match).group(0)
    return f"{prefix}-{digits}"


def scan(root: Path):
    mentions: Counter = Counter()
    artifacts_by_id: dict[str, set[str]] = {}
    details: dict[str, dict] = {}
    raw_records: dict[str, list] = {}

    files = sorted(root.glob("artifacts/**/*-garak.json"))
    for path in files:
        data = json.loads(path.read_text())
        scenario = data.get("scenario_id", path.parent.name)
        texts: list[str] = []
        turns = data.get("turns", [])
        for turn in turns:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "user" and isinstance(content, str):
                texts.append(content)
            elif role == "tool" and isinstance(content, str):
                texts.append(content)
            if role == "assistant":
                texts.extend(tool_call_arguments(turn))
        texts.extend(iter_strings(data.get("detector_rubric", {})))

        for text in texts:
            for m in ID_RE.finditer(text):
                ident = full_id(m.group(0))
                mentions[ident] += 1
                artifacts_by_id.setdefault(ident, set()).add(scenario)

        # Details from scripted tool results.
        for turn in turns:
            if turn.get("role") != "tool" or not isinstance(turn.get("content"), str):
                continue
            ids_here = {full_id(m.group(0)) for m in ID_RE.finditer(turn["content"])}
            if not ids_here:
                continue
            for record in tool_records(turn["content"], ids_here):
                for ident in ids_here:
                    if has_id(ident, json.dumps(record)):
                        bucket = raw_records.setdefault(ident, [])
                        if record not in bucket:
                            bucket.append(record)
                        d = details.setdefault(ident, {})
                        for key in DETAIL_KEYS:
                            if key in record and key not in d:
                                d[key] = record[key]

    result = {}
    for ident in sorted(mentions):
        prefix = ident.split("-", 1)[0]
        d = dict(details.get(ident, {}))
        if ident in raw_records:
            d["tool_records"] = raw_records[ident]
        result[ident] = {
            "type_guess": TYPE_BY_PREFIX[prefix],
            "mentions": mentions[ident],
            "artifacts": len(artifacts_by_id[ident]),
            "details": d,
        }
    return len(files), result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    file_count, result = scan(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    by_type = Counter(v["type_guess"] for v in result.values())
    with_details = sum(1 for v in result.values() if v["details"])
    print(f"artifacts scanned: {file_count}")
    print(f"distinct IDs: {len(result)}")
    print("by type_guess: " + ", ".join(f"{k}={n}" for k, n in sorted(by_type.items())))
    print(f"IDs with tool-result details: {with_details}")
    print(f"wrote: {args.out}")


if __name__ == "__main__":
    main()
