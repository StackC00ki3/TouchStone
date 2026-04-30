"""
Flet tool for reviewing English strings against merged Chinese translations.

Features:
- Read A directory: nethack_strings.json
- Read B directory: nethack_strings_merged.json
- Show source location/snippet for selected A entry with lightweight C syntax highlight
- Find and display all entries in merged data whose `en` matches selected A `en`
- Provide editable zh input for each matched merged entry and save back to JSON
"""

import json
import os
import re
import copy
from typing import Dict, List, Tuple, Any

import flet as ft

CONTEXT_LINES = 6
C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double",
    "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long",
    "register", "restrict", "return", "short", "signed", "sizeof", "static", "struct",
    "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "_Bool",
}
TOKEN_RE = re.compile(
    r"//.*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\b[A-Za-z_][A-Za-z0-9_]*\b"
)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return ["" if x is None else str(x) for x in value]
    if isinstance(value, str):
        return [value]
    if value is None:
        return []
    return [str(value)]


def normalize_array_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return ["" if x is None else str(x) for x in value]


def normalize_args_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for x in value:
        if not isinstance(x, dict):
            continue
        item = dict(x)
        item["idx"] = x.get("idx", "")
        item["en"] = normalize_array_text_list(x.get("en"))
        item["zh"] = normalize_array_text_list(x.get("zh"))
        out.append(item)
    return out


def read_source_lines(base_path: str, rel_file: str, target_line: int) -> Tuple[List[str], int]:
    rel = rel_file.replace("\\", os.sep).replace("/", os.sep)
    full = os.path.join(base_path, rel)
    if not os.path.isfile(full):
        return [], 0
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return [], 0

    start = max(0, target_line - 1 - CONTEXT_LINES)
    end = min(len(all_lines), target_line + CONTEXT_LINES)
    return all_lines[start:end], start + 1


def line_spans_with_highlight(line: str, is_focus: bool) -> List[ft.TextSpan]:
    spans: List[ft.TextSpan] = []
    default_style = ft.TextStyle(color=ft.Colors.ON_SURFACE)
    kw_style = ft.TextStyle(color=ft.Colors.CYAN_300, weight=ft.FontWeight.BOLD)
    str_style = ft.TextStyle(color=ft.Colors.ORANGE_300)
    cmt_style = ft.TextStyle(color=ft.Colors.GREEN_400)

    if is_focus:
        default_style = ft.TextStyle(color=ft.Colors.ON_SURFACE, bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.YELLOW))
        kw_style = ft.TextStyle(
            color=ft.Colors.CYAN_300,
            weight=ft.FontWeight.BOLD,
            bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.YELLOW),
        )
        str_style = ft.TextStyle(color=ft.Colors.ORANGE_300, bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.YELLOW))
        cmt_style = ft.TextStyle(color=ft.Colors.GREEN_400, bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.YELLOW))

    pos = 0
    for m in TOKEN_RE.finditer(line):
        if m.start() > pos:
            spans.append(ft.TextSpan(line[pos:m.start()], style=default_style))
        token = m.group(0)
        if token.startswith("//") or token.startswith("/*"):
            spans.append(ft.TextSpan(token, style=cmt_style))
        elif token.startswith('"') or token.startswith("'"):
            spans.append(ft.TextSpan(token, style=str_style))
        elif token in C_KEYWORDS:
            spans.append(ft.TextSpan(token, style=kw_style))
        else:
            spans.append(ft.TextSpan(token, style=default_style))
        pos = m.end()

    if pos < len(line):
        spans.append(ft.TextSpan(line[pos:], style=default_style))
    return spans


def make_source_spans(lines: List[str], start_line: int, focus_line: int) -> List[ft.TextSpan]:
    if not lines:
        return [ft.TextSpan("  (source file not found)\n", style=ft.TextStyle(color=ft.Colors.RED_300))]

    out: List[ft.TextSpan] = []
    for idx, raw in enumerate(lines):
        ln = start_line + idx
        text = raw.rstrip("\n")
        is_focus = ln == focus_line

        prefix_style = ft.TextStyle(color=ft.Colors.BLUE_GREY_200)
        if is_focus:
            prefix_style = ft.TextStyle(
                color=ft.Colors.YELLOW_200,
                weight=ft.FontWeight.BOLD,
                bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.YELLOW),
            )

        out.append(ft.TextSpan(f"{ln:5d} | ", style=prefix_style))
        out.extend(line_spans_with_highlight(text, is_focus=is_focus))
        out.append(ft.TextSpan("\n", style=prefix_style if is_focus else ft.TextStyle(color=ft.Colors.ON_SURFACE)))

    return out


def flatten_entries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for mode, items in data.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if not isinstance(entry, dict):
                continue
            entries.append(
                {
                    "mode": mode,
                    "key": key,
                    "entry": entry,
                    "en": normalize_text_list(entry.get("en")),
                    "zh": normalize_text_list(entry.get("zh")),
                    "args": normalize_args_list(entry.get("args")),
                    "file": str(entry.get("file", "")),
                    "line": int(entry.get("line", 0) or 0),
                    "func": str(entry.get("func", "")),
                    "occ": entry.get("occ", ""),
                }
            )
    return entries


def build_en_index(entries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    idx: Dict[str, List[Dict[str, Any]]] = {}
    for item in entries:
        for en_text in item["en"]:
            idx.setdefault(en_text, []).append(item)
    return idx


def safe_split_lines(text: str) -> List[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n")


def merge_output_data(a_data: Dict[str, Any], existing_out: Dict[str, Any]) -> Dict[str, Any]:
    """
    Use latest A data as the structural base, but preserve already-saved zh/args.zh
    from nethack_strings_a_modified.json when matching entries exist.
    """
    merged = copy.deepcopy(a_data)
    if not isinstance(existing_out, dict):
        return merged

    for mode, existing_items in existing_out.items():
        if not isinstance(existing_items, dict):
            continue
        target_items = merged.get(mode)
        if not isinstance(target_items, dict):
            continue

        for key, existing_entry in existing_items.items():
            if not isinstance(existing_entry, dict):
                continue
            target_entry = target_items.get(key)
            if not isinstance(target_entry, dict):
                continue

            if "zh" in existing_entry:
                target_entry["zh"] = copy.deepcopy(existing_entry.get("zh"))

            existing_args = existing_entry.get("args")
            target_args = target_entry.get("args")
            if isinstance(existing_args, list) and isinstance(target_args, list):
                for arg_i, existing_arg in enumerate(existing_args):
                    if (
                        0 <= arg_i < len(target_args)
                        and isinstance(existing_arg, dict)
                        and isinstance(target_args[arg_i], dict)
                        and "zh" in existing_arg
                    ):
                        target_args[arg_i]["zh"] = copy.deepcopy(existing_arg.get("zh"))

    return merged


def main(page: ft.Page) -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))

    page.title = "NetHack EN -> merged ZH matcher"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 10
    page.window_min_width = 1200
    page.window_min_height = 760
    # Improve CJK rendering consistency on Windows.
    page.theme = ft.Theme(font_family="Microsoft YaHei UI")
    page.fonts = {
        "ui": "Microsoft YaHei UI, Segoe UI, Noto Sans CJK SC, sans-serif",
        "code": "Cascadia Mono, Consolas, Sarasa Mono SC, Microsoft YaHei UI, monospace",
    }

    state = {
        "a_src_dir": r"D:\CodingFun\CProjects\TouchStone\Nethack",
        "b_src_dir": r"D:\\Download\\Compressed\\NetHack-cn-NetHack-cn\\NetHack",
        "a_path": "",
        "b_path": "",
        "out_path": "",
        "a_data": {},
        "b_data": {},
        "out_data": {},
        "a_entries": [],
        "b_entries": [],
        "out_entry_map": {},
        "b_index": {},
        "filtered_indices": [],
        "cursor": 0,
    }

    a_dir_input = ft.TextField(label="A 源码目录", value=state["a_src_dir"], expand=True)
    b_dir_input = ft.TextField(label="B 源码目录", value=state["b_src_dir"], expand=True)
    mode_filter = ft.Dropdown(
        label="函数名",
        width=220,
        value="__ALL__",
        options=[ft.dropdown.Option("__ALL__", "全部")],
    )
    filter_input = ft.TextField(label="筛选 EN / key / file", expand=True)

    status = ft.Text("请先加载 A/B 目录", size=13)
    nav_input = ft.TextField(
        value="0",
        width=72,
        height=40,
        text_align=ft.TextAlign.CENTER,
        keyboard_type=ft.KeyboardType.NUMBER,
        dense=True,
        tooltip="输入条目序号后回车跳转",
    )
    nav_total = ft.Text("/ 0", size=14, weight=ft.FontWeight.BOLD)

    current_header = ft.Text("", selectable=True, size=13)
    current_en = ft.Text("", selectable=True, size=14, color=ft.Colors.LIGHT_BLUE_200)
    current_source = ft.Text(
        spans=[], selectable=True, font_family="code", size=12, no_wrap=True
    )

    matches_column = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=8, expand=True)

    def selected_a_entry() -> Dict[str, Any]:
        indices = state["filtered_indices"]
        if not indices:
            return {}
        cur = state["cursor"]
        if cur < 0 or cur >= len(indices):
            return {}
        return state["a_entries"][indices[cur]]

    def refresh_filter() -> None:
        query = (filter_input.value or "").strip().lower()
        selected_mode = mode_filter.value or "__ALL__"
        state["filtered_indices"] = []
        for i, item in enumerate(state["a_entries"]):
            if selected_mode != "__ALL__" and item["mode"] != selected_mode:
                continue
            if not query:
                state["filtered_indices"].append(i)
                continue
            hay = "\n".join(item["en"] + [item["key"], item["file"], item["mode"]]).lower()
            if query in hay:
                state["filtered_indices"].append(i)
        state["cursor"] = 0

    def refresh_mode_filter_options() -> None:
        modes = sorted({item.get("mode", "") for item in state["a_entries"] if item.get("mode", "")})
        options = [ft.dropdown.Option("__ALL__", "全部")]
        for mode in modes:
            options.append(ft.dropdown.Option(mode, mode))
        mode_filter.options = options
        current = mode_filter.value or "__ALL__"
        valid_values = {opt.key for opt in options}
        mode_filter.value = current if current in valid_values else "__ALL__"

    def refresh_current_panel() -> None:
        item = selected_a_entry()
        if not item:
            nav_input.value = "0"
            nav_total.value = "/ 0"
            current_header.value = ""
            current_en.value = ""
            current_source.spans = []
            matches_column.controls = [ft.Text("没有可显示的条目", color=ft.Colors.RED_300)]
            page.update()
            return

        nav_input.value = str(state["cursor"] + 1)
        nav_total.value = f"/ {len(state['filtered_indices'])}"
        current_header.value = (
            f"A | mode={item['mode']} key={item['key']} | {item['file']}:{item['line']} "
            f"func={item['func']} occ={item['occ']}"
        )
        current_en.value = "\n".join(item["en"])

        if item["file"] and item["line"] > 0:
            lines, start_line = read_source_lines(state["a_src_dir"], item["file"], item["line"])
            current_source.spans = make_source_spans(lines, start_line, item["line"])
        else:
            current_source.spans = [
                ft.TextSpan("  (no file/line in A entry)\n", style=ft.TextStyle(color=ft.Colors.RED_300))
            ]

        render_matches(item)

    def write_output() -> bool:
        try:
            save_json(state["out_path"], state["out_data"])
            return True
        except OSError as ex:
            status.value = f"保存失败: {ex}"
            return False

    def render_matches(a_item: Dict[str, Any]) -> None:
        out_key = (a_item["mode"], a_item["key"])
        current_out_entry = state["out_entry_map"].get(out_key) if out_key else None
        current_out_zh = normalize_text_list(current_out_entry.get("zh")) if isinstance(current_out_entry, dict) else []
        current_out_args = (
            normalize_args_list(current_out_entry.get("args")) if isinstance(current_out_entry, dict) else []
        )

        seen = set()
        matched: List[Dict[str, Any]] = []
        for en_text in a_item["en"]:
            for candidate in state["b_index"].get(en_text, []):
                uniq = (candidate["mode"], candidate["key"])
                if uniq in seen:
                    continue
                seen.add(uniq)
                matched.append(candidate)

        matched.sort(key=lambda x: (x["mode"], x["file"], x["line"], x["key"]))

        controls: List[ft.Control] = []
        controls.append(ft.Text(f"匹配项: {len(matched)}", weight=ft.FontWeight.BOLD))

        if not matched:
            controls.append(ft.Text("在 merged 中没有找到匹配 en 的条目", color=ft.Colors.RED_300))
            matches_column.controls = controls
            page.update()
            return

        for m in matched:
            file_line = f"{m['file']}:{m['line']}" if m["file"] else "(no file)"
            meta = ft.Text(
                f"B | mode={m['mode']} key={m['key']} | {file_line} func={m['func']} occ={m['occ']}",
                selectable=True,
                size=12,
            )
            en_text = ft.Text("\n".join(m["en"]), selectable=True, color=ft.Colors.LIGHT_BLUE_100, size=13)

            initial_zh = current_out_zh if current_out_zh else m["zh"]
            zh_input = ft.TextField(
                label="手动修改 zh（每行一个元素）",
                value="\n".join(initial_zh),
                multiline=True,
                min_lines=2,
                max_lines=6,
                text_size=13,
            )

            arg_items = normalize_args_list(m["entry"].get("args"))
            arg_editors: List[Tuple[int, ft.TextField]] = []
            arg_controls: List[ft.Control] = []
            if arg_items:
                for arg_i, arg in enumerate(arg_items):
                    arg_idx = arg.get("idx", "")
                    arg_en = normalize_text_list(arg.get("en"))
                    arg_zh = normalize_text_list(arg.get("zh"))
                    if arg_i < len(current_out_args):
                        out_arg_zh = normalize_text_list(current_out_args[arg_i].get("zh"))
                        if out_arg_zh:
                            arg_zh = out_arg_zh
                    arg_zh_input = ft.TextField(
                        label=f"args[{arg_i}].zh（每行一个元素）",
                        value="\n".join(arg_zh),
                        multiline=True,
                        min_lines=2,
                        max_lines=6,
                        expand=True,
                        text_size=12,
                    )
                    arg_editors.append((arg_i, arg_zh_input))
                    arg_controls.append(
                        ft.Row(
                            [
                                ft.Container(
                                    width=360,
                                    content=ft.Text(
                                        f"idx={arg_idx} | en={json.dumps(arg_en, ensure_ascii=False)}",
                                        selectable=True,
                                        size=12,
                                        color=ft.Colors.ORANGE_200,
                                    ),
                                ),
                                arg_zh_input,
                            ],
                            spacing=8,
                        )
                    )

            src_text = ft.Text(
                spans=[],
                selectable=True,
                font_family="code",
                size=11,
                no_wrap=True,
            )
            if m["file"] and m["line"] > 0:
                lines, start_line = read_source_lines(state["b_src_dir"], m["file"], m["line"])
                src_text.spans = make_source_spans(lines, start_line, m["line"])
            else:
                src_text.spans = [
                    ft.TextSpan("  (no file/line in merged entry)\n", style=ft.TextStyle(color=ft.Colors.RED_300))
                ]

            def on_save_click(_, target=m, editor=zh_input, editors=arg_editors):
                # 将本次编辑应用到 A 的当前条目（基于 A 的拷贝 out_data）
                a_cur = selected_a_entry()
                if not a_cur:
                    status.value = "保存失败: 当前没有选中的 A 条目"
                    page.update()
                    return

                out_key = (a_cur["mode"], a_cur["key"])
                out_entry = state["out_entry_map"].get(out_key)
                if not isinstance(out_entry, dict):
                    status.value = f"保存失败: 在输出数据中找不到 A 条目 {a_cur['mode']}:{a_cur['key']}"
                    page.update()
                    return

                out_entry["zh"] = safe_split_lines(editor.value or "")

                args_value = out_entry.get("args")
                if isinstance(args_value, list):
                    for arg_i, arg_editor in editors:
                        if 0 <= arg_i < len(args_value) and isinstance(args_value[arg_i], dict):
                            args_value[arg_i]["zh"] = safe_split_lines(arg_editor.value or "")

                # 同步刷新当前 A 条目的显示数据
                a_cur["entry"] = out_entry
                a_cur["zh"] = normalize_text_list(out_entry.get("zh"))
                a_cur["args"] = normalize_args_list(out_entry.get("args"))

                if write_output():
                    status.value = (
                        f"已保存到独立文件: {state['out_path']} | "
                        f"A {a_cur['mode']}:{a_cur['key']} | 来源 B {target['mode']}:{target['key']}"
                    )
                page.update()

            save_btn = ft.FilledButton("保存该匹配项 zh", on_click=on_save_click)

            card = ft.Container(
                bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.BLUE_GREY),
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8,
                padding=10,
                content=ft.Column(
                    [
                        meta,
                        ft.Container(
                            padding=8,
                            border_radius=6,
                            bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.LIGHT_BLUE),
                            content=en_text,
                        ),
                        zh_input,
                        ft.Text("args 手动修改:", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Column(
                            controls=arg_controls
                            if arg_controls
                            else [ft.Text("该匹配项无 args", size=12, color=ft.Colors.BLUE_GREY_300)],
                            spacing=6,
                        ),
                        save_btn,
                        ft.Text("源码:", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Container(
                            height=180,
                            border_radius=6,
                            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
                            padding=8,
                            content=ft.Row([src_text], scroll=ft.ScrollMode.AUTO),
                        ),
                    ],
                    spacing=8,
                ),
            )
            controls.append(card)

        matches_column.controls = controls
        page.update()

    def on_load(_):
        a_src_dir = (a_dir_input.value or "").strip().strip('"')
        b_src_dir = (b_dir_input.value or "").strip().strip('"')
        a_json = os.path.join(base_dir, "nethack_strings.json")
        b_json = os.path.join(base_dir, "nethack_strings_merged.json")
        out_json = os.path.join(base_dir, "nethack_strings_a_modified.json")

        if a_src_dir and not os.path.isdir(a_src_dir):
            status.value = f"A 源码目录不存在: {a_src_dir}"
            page.update()
            return
        if b_src_dir and not os.path.isdir(b_src_dir):
            status.value = f"B 源码目录不存在: {b_src_dir}"
            page.update()
            return

        missing = []
        if not os.path.isfile(a_json):
            missing.append(f"缺少: {a_json}")
        if not os.path.isfile(b_json):
            missing.append(f"缺少: {b_json}")
        if missing:
            status.value = " | ".join(missing)
            page.update()
            return

        try:
            a_data = load_json(a_json)
            b_data = load_json(b_json)
        except (OSError, json.JSONDecodeError) as ex:
            status.value = f"加载失败: {ex}"
            page.update()
            return

        existing_out_data: Dict[str, Any] = {}
        if os.path.isfile(out_json):
            try:
                existing_out_data = load_json(out_json)
            except (OSError, json.JSONDecodeError) as ex:
                status.value = f"加载已有输出文件失败: {ex}"
                page.update()
                return

        state["a_src_dir"] = a_src_dir
        state["b_src_dir"] = b_src_dir
        state["a_path"] = a_json
        state["b_path"] = b_json
        state["out_path"] = out_json
        state["a_data"] = a_data
        state["b_data"] = b_data
        state["out_data"] = merge_output_data(a_data, existing_out_data)
        state["a_entries"] = flatten_entries(state["out_data"])
        state["b_entries"] = flatten_entries(b_data)
        state["b_index"] = build_en_index(state["b_entries"])
        refresh_mode_filter_options()

        out_entry_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for mode, items in state["out_data"].items():
            if not isinstance(items, dict):
                continue
            for key, entry in items.items():
                if isinstance(entry, dict):
                    out_entry_map[(mode, key)] = entry
        state["out_entry_map"] = out_entry_map

        if not os.path.isfile(out_json):
            try:
                save_json(out_json, state["out_data"])
            except OSError as ex:
                status.value = f"初始化输出文件失败: {ex}"
                page.update()
                return

        refresh_filter()
        status.value = (
            f"加载完成 | A entries={len(state['a_entries'])} | "
            f"merged entries={len(state['b_entries'])} | 输出文件={state['out_path']} | "
            f"JSON目录={base_dir}"
        )
        refresh_current_panel()

    def on_prev(_):
        if not state["filtered_indices"]:
            return
        state["cursor"] = max(0, state["cursor"] - 1)
        refresh_current_panel()

    def on_next(_):
        if not state["filtered_indices"]:
            return
        state["cursor"] = min(len(state["filtered_indices"]) - 1, state["cursor"] + 1)
        refresh_current_panel()

    def on_nav_submit(_):
        total = len(state["filtered_indices"])
        if total <= 0:
            nav_input.value = "0"
            nav_total.value = "/ 0"
            page.update()
            return

        raw = (nav_input.value or "").strip()
        if not raw:
            nav_input.value = str(state["cursor"] + 1)
            page.update()
            return

        try:
            target = int(raw)
        except ValueError:
            status.value = f"跳转失败: 请输入 1 到 {total} 的数字"
            nav_input.value = str(state["cursor"] + 1)
            page.update()
            return

        if target < 1 or target > total:
            status.value = f"跳转失败: 请输入 1 到 {total} 的数字"
            nav_input.value = str(state["cursor"] + 1)
            page.update()
            return

        state["cursor"] = target - 1
        refresh_current_panel()

    def on_filter_change(_):
        refresh_filter()
        refresh_current_panel()

    load_btn = ft.FilledButton("加载 A/B", icon=ft.Icons.FOLDER_OPEN, on_click=on_load)
    prev_btn = ft.IconButton(ft.Icons.ARROW_BACK, tooltip="上一条", on_click=on_prev)
    next_btn = ft.IconButton(ft.Icons.ARROW_FORWARD, tooltip="下一条", on_click=on_next)

    filter_input.on_submit = on_filter_change
    nav_input.on_submit = on_nav_submit
    mode_filter.on_change = on_filter_change
    filter_btn = ft.OutlinedButton("应用筛选", on_click=on_filter_change)

    left_panel = ft.Container(
        expand=1,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=8,
        padding=10,
        content=ft.Column(
            [
                ft.Text("A 条目", size=15, weight=ft.FontWeight.BOLD),
                current_header,
                ft.Container(
                    padding=8,
                    border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.LIGHT_BLUE),
                    content=current_en,
                ),
                ft.Text("A 源码:", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Container(
                    expand=True,
                    border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
                    padding=8,
                    content=ft.Row([current_source], scroll=ft.ScrollMode.AUTO),
                ),
            ],
            spacing=8,
            expand=True,
        ),
    )

    right_panel = ft.Container(
        expand=1,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=8,
        padding=10,
        content=ft.Column(
            [
                ft.Text("merged 匹配项", size=15, weight=ft.FontWeight.BOLD),
                matches_column,
            ],
            spacing=8,
            expand=True,
        ),
    )

    page.add(
        ft.Row([a_dir_input, b_dir_input, load_btn], spacing=8),
        ft.Row(
            [mode_filter, filter_input, filter_btn, prev_btn, nav_input, nav_total, next_btn],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        ft.Row([left_panel, right_panel], expand=True, spacing=10),
        status,
    )


if __name__ == "__main__":
    ft.run(main)
