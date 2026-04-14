"""
NetHack strings.json 比较工具
比较两个路径下的 nethack_strings.json，并排显示翻译差异和源代码上下文。
用法: python compare_strings.py
"""

import json
import os
import copy
import flet as ft

CONTEXT_LINES = 5  # 源代码上下文行数（前后各几行）


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_source_lines(base_path, rel_file, target_line):
    """读取源文件中 target_line 附近的行，返回 (lines, start_line)"""
    # rel_file 可能用反斜杠
    rel_file = rel_file.replace("\\", os.sep).replace("/", os.sep)
    full = os.path.join(base_path, rel_file)
    if not os.path.isfile(full):
        return None, 0
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception:
        return None, 0
    start = max(0, target_line - 1 - CONTEXT_LINES)
    end = min(len(all_lines), target_line + CONTEXT_LINES)
    return all_lines[start:end], start + 1


def make_source_span(lines, start_line, highlight_line):
    """构造带高亮的源代码 TextSpan 列表"""
    spans = []
    if lines is None:
        spans.append(ft.TextSpan("  (文件未找到)\n", ft.TextStyle(color=ft.Colors.RED_300)))
        return spans
    for i, line in enumerate(lines):
        ln = start_line + i
        prefix = f"{ln:5d} | "
        text = line.rstrip("\n")
        if ln == highlight_line:
            spans.append(ft.TextSpan(
                f"{prefix}{text}\n",
                ft.TextStyle(
                    color=ft.Colors.YELLOW,
                    weight=ft.FontWeight.BOLD,
                    bgcolor=ft.Colors.with_opacity(0.25, ft.Colors.YELLOW),
                ),
            ))
        else:
            spans.append(ft.TextSpan(
                f"{prefix}{text}\n",
                ft.TextStyle(color=ft.Colors.ON_SURFACE_VARIANT),
            ))
    return spans


def main(page: ft.Page):
    page.title = "NetHack Strings 比较工具"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 10

    # ── 状态 ──
    data_a: dict = {}
    data_b: dict = {}
    path_a_val = r"D:\Download\nethack-367-src\nethack-367-src\NetHack-3.6.7"
    path_b_val = r"D:\Download\Compressed\NetHack-cn-NetHack-cn\NetHack"
    current_mode = ""
    common_keys: list = []
    current_index = 0
    filter_mode = "all"  # all / diff_only

    # ── UI 控件 ──
    path_a_field = ft.TextField(value= path_a_val, label="路径 A", expand=True, text_size=13)
    path_b_field = ft.TextField(value= path_b_val, label="路径 B", expand=True, text_size=13)
    mode_dropdown = ft.Dropdown(label="模式", width=200, options=[], disabled=True)
    status_text = ft.Text("请加载两个路径", size=13)
    nav_label = ft.Text("0 / 0", size=14, weight=ft.FontWeight.BOLD)
    filter_dropdown = ft.Dropdown(
        label="过滤",
        width=160,
        value="all",
        options=[
            ft.dropdown.Option("all", "全部"),
            ft.dropdown.Option("diff_only", "仅差异"),
        ],
    )

    key_label = ft.Text("", size=13, selectable=True, color=ft.Colors.SECONDARY)
    info_a = ft.Text("", size=13, selectable=True)
    info_b = ft.Text("", size=13, selectable=True)
    en_a = ft.Text("", size=14, selectable=True, color=ft.Colors.LIGHT_BLUE_200)
    en_b = ft.Text("", size=14, selectable=True, color=ft.Colors.LIGHT_GREEN_200)
    src_a = ft.Text(spans=[], selectable=True, size=12, font_family="Consolas, monospace", no_wrap=True)
    src_b = ft.Text(spans=[], selectable=True, size=12, font_family="Consolas, monospace", no_wrap=True)
    diff_badge = ft.Container(
        content=ft.Text("相同", size=11, color=ft.Colors.WHITE),
        bgcolor=ft.Colors.GREEN_700,
        padding=ft.Padding.symmetric(vertical=4, horizontal=8),
        border_radius=4,
        visible=False,
    )
    jump_field = ft.TextField(label="跳转", width=100, text_size=13, input_filter=ft.NumbersOnlyInputFilter())

    def recompute_common_keys():
        nonlocal common_keys, current_index
        if not current_mode or current_mode not in data_a or current_mode not in data_b:
            common_keys = []
            current_index = 0
            return
        keys_a = data_a[current_mode]
        keys_b = data_b[current_mode]
        all_common = [k for k in keys_a if k in keys_b]
        if filter_mode == "diff_only":
            common_keys = [k for k in all_common if keys_a[k].get("en") != keys_b[k].get("zh")]
        else:
            common_keys = all_common
        current_index = 0

    def show_entry():
        if not common_keys:
            nav_label.value = "0 / 0"
            key_label.value = ""
            info_a.value = info_b.value = ""
            en_a.value = en_b.value = ""
            src_a.spans = []
            src_b.spans = []
            diff_badge.visible = False
            status_text.value = "没有匹配的条目"
            page.update()
            return

        k = common_keys[current_index]
        entry_a = data_a[current_mode][k]
        entry_b = data_b[current_mode][k]
        nav_label.value = f"{current_index + 1} / {len(common_keys)}"
        key_label.value = f"Key: {k}"
        info_a.value = f"{entry_a['file']}:{entry_a['line']}  func={entry_a.get('func','')}  occ={entry_a.get('occ','')}"
        info_b.value = f"{entry_b['file']}:{entry_b['line']}  func={entry_b.get('func','')}  occ={entry_b.get('occ','')}"
        en_a.value = entry_a.get("en", "")
        en_b.value = entry_b.get("zh", "")

        is_diff = en_a.value != en_b.value
        diff_badge.visible = True
        if is_diff:
            diff_badge.content.value = "不同"
            diff_badge.bgcolor = ft.Colors.RED_700
        else:
            diff_badge.content.value = "相同"
            diff_badge.bgcolor = ft.Colors.GREEN_700

        # 源代码
        lines_a, start_a = read_source_lines(path_a_val, entry_a["file"], entry_a["line"])
        lines_b, start_b = read_source_lines(path_b_val, entry_b["file"], entry_b["line"])
        src_a.spans = make_source_span(lines_a, start_a, entry_a["line"])
        src_b.spans = make_source_span(lines_b, start_b, entry_b["line"])

        status_text.value = f"模式: {current_mode} | 共同 key: {len(common_keys)}"
        page.update()

    def on_load(e):
        nonlocal data_a, data_b, path_a_val, path_b_val, current_mode
        pa = path_a_field.value.strip().strip('"')
        pb = path_b_field.value.strip().strip('"')
        ja = os.path.join(pa, "nethack_strings.json")
        jb = os.path.join(pb, "nethack_strings.json")
        errors = []
        if not os.path.isfile(ja):
            errors.append(f"找不到: {ja}")
        if not os.path.isfile(jb):
            errors.append(f"找不到: {jb}")
        if errors:
            status_text.value = " | ".join(errors)
            page.update()
            return
        path_a_val = pa
        path_b_val = pb
        data_a = load_json(ja)
        data_b = load_json(jb)
        modes = sorted(set(data_a.keys()) | set(data_b.keys()))
        mode_dropdown.options = [ft.dropdown.Option(m) for m in modes]
        mode_dropdown.disabled = False
        if modes:
            mode_dropdown.value = modes[0]
            current_mode = modes[0]
            recompute_common_keys()
        status_text.value = f"已加载 | A 模式: {list(data_a.keys())} | B 模式: {list(data_b.keys())}"
        show_entry()

    def on_merge_export(e):
        if not data_a or not data_b:
            status_text.value = "请先加载两个路径后再导出"
            page.update()
            return

        merged = {}
        mode_count = 0
        item_count = 0

        for mode, left_items in data_a.items():
            right_items = data_b.get(mode)
            if not isinstance(left_items, dict) or not isinstance(right_items, dict):
                continue

            merged_mode = {}
            for key, left_entry in left_items.items():
                right_entry = right_items.get(key)
                if not isinstance(left_entry, dict) or not isinstance(right_entry, dict):
                    continue

                zh_text = right_entry.get("zh", "")
                if left_entry.get("en", "") == zh_text:
                    continue

                item = copy.deepcopy(left_entry)
                item["zh"] = zh_text
                merged_mode[key] = item
                item_count += 1

            if merged_mode:
                merged[mode] = merged_mode
                mode_count += 1

        out_path = os.path.join(os.getcwd(), "nethack_strings_merged.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

        status_text.value = f"导出完成: {out_path} | 模式: {mode_count} | 条目: {item_count}"
        page.update()

    def on_mode_change(e):
        nonlocal current_mode
        current_mode = mode_dropdown.value or ""
        recompute_common_keys()
        show_entry()

    def on_filter_change(e):
        nonlocal filter_mode
        filter_mode = filter_dropdown.value or "all"
        recompute_common_keys()
        show_entry()

    def on_prev(e):
        nonlocal current_index
        if common_keys and current_index > 0:
            current_index -= 1
            show_entry()

    def on_next(e):
        nonlocal current_index
        if common_keys and current_index < len(common_keys) - 1:
            current_index += 1
            show_entry()

    def on_jump(e):
        nonlocal current_index
        try:
            idx = int(jump_field.value) - 1
            if 0 <= idx < len(common_keys):
                current_index = idx
                show_entry()
        except (ValueError, TypeError):
            pass

    def on_keyboard(e: ft.KeyboardEvent):
        nonlocal current_index
        if e.key == "A" or e.key == "[":
            on_prev(None)
        elif e.key == "D" or e.key == "]":
            on_next(None)

    page.on_keyboard_event = on_keyboard

    # Flet 0.8x uses `on_select` for Dropdown selection events.
    mode_dropdown.on_select = on_mode_change
    filter_dropdown.on_select = on_filter_change

    load_btn = ft.Button("加载", on_click=on_load, icon=ft.Icons.FOLDER_OPEN)
    merge_btn = ft.Button("合并导出", on_click=on_merge_export, icon=ft.Icons.DOWNLOAD)
    prev_btn = ft.IconButton(ft.Icons.ARROW_BACK, on_click=on_prev, tooltip="上一个 (A / [)")
    next_btn = ft.IconButton(ft.Icons.ARROW_FORWARD, on_click=on_next, tooltip="下一个 (D / ])")
    jump_btn = ft.TextButton("跳转", on_click=on_jump)

    # ── 布局 ──
    toolbar = ft.Row([
        path_a_field, path_b_field, load_btn, merge_btn,
    ], spacing=8)

    controls_row = ft.Row([
        mode_dropdown, filter_dropdown,
        ft.VerticalDivider(width=1),
        prev_btn, nav_label, next_btn,
        ft.VerticalDivider(width=1),
        jump_field, jump_btn,
        diff_badge,
    ], alignment=ft.MainAxisAlignment.START, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def make_panel(title, info, en_text, src_text, color):
        return ft.Container(
            expand=True,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=6,
            padding=10,
            content=ft.Column([
                ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=color),
                info,
                ft.Container(
                    content=en_text,
                    bgcolor=ft.Colors.with_opacity(0.08, color),
                    padding=8,
                    border_radius=4,
                ),
                ft.Text("源代码:", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Container(
                    content=ft.Row(
                        [src_text],
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
                    padding=8,
                    border_radius=4,
                    height=250,
                ),
            ], spacing=6, scroll=ft.ScrollMode.AUTO),
        )

    panel_a = make_panel("A (左)", info_a, en_a, src_a, ft.Colors.LIGHT_BLUE_200)
    panel_b = make_panel("B (右)", info_b, en_b, src_b, ft.Colors.LIGHT_GREEN_200)

    compare_row = ft.Row([panel_a, panel_b], spacing=10, expand=True)

    page.add(
        toolbar,
        controls_row,
        key_label,
        compare_row,
        status_text,
    )


if __name__ == "__main__":
    ft.run(main)
