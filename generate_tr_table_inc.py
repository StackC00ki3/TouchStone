#!/usr/bin/env python3
"""Generate compile-time C include tables from NetHack translation JSON files.

Outputs:
1. Exact table for source-location keyed translations:
   { "<digest>:en:<i>", "<zh>" },
   { "<digest>:arg:<idx>:<j>", "<zh>" },

2. Category table for appendix dictionaries:
   { "<category>", "<source>", "<zh>" },

3. Assign replacement table for putmesg_final partial replacements:
   { "<en>", "<zh>", <len> },

The category table is generic: new top-level categories can be added to
appendix JSON without any C code changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def c_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def add_if_translated(rows: dict[tuple[str, ...], str], key: tuple[str, ...], source: Any, value: Any) -> None:
    if not isinstance(value, str):
        return
    if isinstance(source, str) and source == value:
        return
    if value or (isinstance(source, str) and source):
        rows[key] = value


def merge_exact_rows(rows: dict[tuple[str], str], data: dict[str, Any]) -> None:
    for section in data.values():
        if not isinstance(section, dict):
            continue

        for digest, entry in section.items():
            if not isinstance(entry, dict):
                continue

            en_list = entry.get("en")
            zh_list = entry.get("zh")
            if isinstance(zh_list, list):
                for i, txt in enumerate(zh_list):
                    src = en_list[i] if isinstance(en_list, list) and i < len(en_list) else None
                    add_if_translated(rows, (f"{digest}:en:{i}",), src, txt)

            args = entry.get("args")
            if isinstance(args, list):
                for arg in args:
                    if not isinstance(arg, dict):
                        continue
                    idx = arg.get("idx")
                    en_arg = arg.get("en")
                    zh_arg = arg.get("zh")
                    if not isinstance(idx, int) or not isinstance(zh_arg, list):
                        continue
                    for j, txt in enumerate(zh_arg):
                        src = en_arg[j] if isinstance(en_arg, list) and j < len(en_arg) else None
                        add_if_translated(rows, (f"{digest}:arg:{idx}:{j}",), src, txt)


def merge_category_rows(rows: dict[tuple[str, str], str], data: dict[str, Any]) -> None:
    for category, section in data.items():
        if not isinstance(category, str) or not isinstance(section, dict):
            continue

        for source_text, value in section.items():
            if isinstance(source_text, str):
                add_if_translated(rows, (category, source_text), source_text, value)


def merge_assign_rows(rows: dict[tuple[str, str], None], data: dict[str, Any]) -> None:
    section = data.get("assign")
    if not isinstance(section, dict):
        return

    def add_pairs(en_list: Any, zh_list: Any) -> None:
        if not isinstance(en_list, list) or not isinstance(zh_list, list):
            return
        for source_text, value in zip(en_list, zh_list):
            if not isinstance(source_text, str) or not isinstance(value, str):
                continue
            if source_text == value:
                continue
            if value or source_text:
                rows[(source_text, value)] = None

    for entry in section.values():
        if not isinstance(entry, dict):
            continue

        add_pairs(entry.get("en"), entry.get("zh"))

        args = entry.get("args")
        if not isinstance(args, list):
            continue
        for arg in args:
            if not isinstance(arg, dict):
                continue
            add_pairs(arg.get("en"), arg.get("zh"))


def build_exact_rows(data: dict[str, Any]) -> list[tuple[str, str]]:
    by_key: dict[tuple[str], str] = {}
    merge_exact_rows(by_key, data)
    return sorted(((key[0], value) for key, value in by_key.items()), key=lambda kv: kv[0])


def build_category_rows(*datasets: dict[str, Any]) -> list[tuple[str, str, str]]:
    by_key: dict[tuple[str, str], str] = {}

    for data in datasets:
        merge_category_rows(by_key, data)

    return sorted(((category, source, value) for (category, source), value in by_key.items()),
                  key=lambda row: (row[0], row[1]))


def build_assign_rows(data: dict[str, Any]) -> list[tuple[str, str, int]]:
    by_key: dict[tuple[str, str], None] = {}
    merge_assign_rows(by_key, data)
    return sorted(
        ((source, value, len(source)) for (source, value) in by_key),
        key=lambda row: (-row[2], row[0], row[1]),
    )


def write_exact_output(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("/* Auto-generated by generate_tr_table_inc.py. Do not edit manually. */\n")
        for key, value in rows:
            f.write(f'{{ "{c_escape(key)}", "{c_escape(value)}" }},\n')


def write_category_output(path: Path, rows: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("/* Auto-generated by generate_tr_table_inc.py. Do not edit manually. */\n")
        for category, source, value in rows:
            f.write(
                f'{{ "{c_escape(category)}", "{c_escape(source)}", "{c_escape(value)}" }},\n'
            )


def write_assign_output(path: Path, rows: list[tuple[str, str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("/* Auto-generated by generate_tr_table_inc.py. Do not edit manually. */\n")
        for source, value, source_len in rows:
            f.write(f'{{ "{c_escape(source)}", "{c_escape(value)}", {source_len} }},\n')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate exact/category/assign translation include tables"
    )
    parser.add_argument(
        "-i",
        "--input",
        default="nethack_strings_translated.json",
        help="Input structured translation json path",
    )
    parser.add_argument(
        "-a",
        "--appendix-input",
        action="append",
        default=["nethack_strings_appendix.json"],
        help="Appendix/category json path; may be passed multiple times",
    )
    parser.add_argument(
        "-c",
        "--custom-input",
        action="append",
        default=[],
        help="Alias of --appendix-input for compatibility; may be passed multiple times",
    )
    parser.add_argument(
        "-o",
        "--output",
        "--exact-output",
        dest="exact_output",
        default="mods/zh_translate/tr_table_exact.inc",
        help="Exact-table output C include path",
    )
    parser.add_argument(
        "--category-output",
        default="mods/zh_translate/tr_table_category.inc",
        help="Category-table output C include path",
    )
    parser.add_argument(
        "--assign-output",
        default="mods/zh_translate/tr_table_assign.inc",
        help="Assign-table output C include path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    exact_input_path = Path(args.input)
    exact_output_path = Path(args.exact_output)
    category_output_path = Path(args.category_output)
    assign_output_path = Path(args.assign_output)

    appendix_inputs = list(args.appendix_input) + list(args.custom_input)
    if not appendix_inputs:
        default_appendix = Path("nethack_strings_appendix.json")
        if default_appendix.exists():
            appendix_inputs.append(str(default_appendix))

    exact_data = json.loads(exact_input_path.read_text(encoding="utf-8"))
    appendix_datasets = [
        json.loads(Path(path).read_text(encoding="utf-8"))
        for path in appendix_inputs
        if Path(path).exists()
    ]

    exact_rows = build_exact_rows(exact_data)
    category_rows = build_category_rows(*appendix_datasets)
    assign_rows = build_assign_rows(exact_data)

    write_exact_output(exact_output_path, exact_rows)
    write_category_output(category_output_path, category_rows)
    write_assign_output(assign_output_path, assign_rows)

    print(f"wrote {len(exact_rows)} exact entries to {exact_output_path}")
    print(f"wrote {len(category_rows)} category entries to {category_output_path}")
    print(f"wrote {len(assign_rows)} assign entries to {assign_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
