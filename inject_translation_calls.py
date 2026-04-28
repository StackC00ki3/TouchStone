import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import clang.cindex

from clang_string_literals import get_string_literal_cursors

# If libclang is not in PATH, uncomment and adjust this line.
clang.cindex.Config.set_library_file(r"D:\Scoop\apps\llvm\22.1.1\bin\libclang.dll")

SKIP_FILES = {
    'mdlib.c',
    'date.c',
    'isaac64.c',
    'hacklib.c',
}

PARSE_ARGS = ('-Inethack/include', '-Iinclude', '-DNETHACK', '-DRELEASE')


@dataclass(frozen=True)
class CallRule:
    bucket: str
    text_arg_index: int
    require_direct_callee: bool = False
    include_extra_args: bool = False


CALL_RULES = {
    'pline': CallRule('pline', text_arg_index=0, require_direct_callee=True, include_extra_args=True),
    'you': CallRule('You', text_arg_index=0, include_extra_args=True),
    'your': CallRule('Your', text_arg_index=0, include_extra_args=True),
    'you_feel': CallRule('You_feel', text_arg_index=0, include_extra_args=True),
    'you_cant': CallRule('You_cant', text_arg_index=0, include_extra_args=True),
    'pline_the': CallRule('pline_The', text_arg_index=0, include_extra_args=True),
    'there': CallRule('There', text_arg_index=0, include_extra_args=True),
    'you_hear': CallRule('You_hear', text_arg_index=0, include_extra_args=True),
    'you_see': CallRule('You_see', text_arg_index=0, include_extra_args=True),
    'strcpy': CallRule('strcpy', text_arg_index=1),
    'strcat': CallRule('strcat', text_arg_index=1),
    'sprintf': CallRule('sprintf', text_arg_index=1, include_extra_args=True),
    'end_menu': CallRule('end_menu', text_arg_index=1),
    'add_menu': CallRule('add_menu', text_arg_index=7),
}

class TranslationInjector:
    def __init__(
        self,
        project_root: str,
        db_path: str,
        translator_func: str,
        clang_args: List[str],
    ):
        self.project_root = os.path.abspath(project_root)
        self.db_path = db_path
        self.translator_func = translator_func
        self.clang_args = clang_args
        self.index = clang.cindex.Index.create()
        self._current_content_bytes = b""
        self._file_bytes: Dict[str, bytes] = {}
        self._file_lines: Dict[str, List[str]] = {}

        self.ctx_by_file: Dict[str, Dict[Tuple[int, int, str], str]] = {}
        self._load_ctx_index()

    def should_process_file(self, file_path: str) -> bool:
        """判断是否需要处理此文件"""
        filename = os.path.basename(file_path)
        return filename not in SKIP_FILES

    @staticmethod
    def _norm_rel_path(path: str) -> str:
        return os.path.normpath(path)

    def _load_ctx_index(self) -> None:
        with open(self.db_path, "r", encoding="utf-8") as f:
            db = json.load(f)

        for callee in db.keys():
            sec = db.get(callee, {})
            for ctx_id, entry in sec.items():
                rel_file = self._norm_rel_path(entry["file"])
                line = int(entry["line"])
                col = int(entry["col"])
                key = (line, col, callee)
                per_file = self.ctx_by_file.setdefault(rel_file, {})
                per_file[key] = ctx_id

    def get_string_literals(self, node, file_path: str = None):
        """递归查找节点下的所有字符串字面量（按出现顺序）"""
        return get_string_literal_cursors(node, file_bytes=self._file_bytes, file_path=file_path)

    def _get_identifier_at_location(self, file_path: str, line: int, col: int) -> Optional[str]:
        lines = self._file_lines.get(file_path)
        if not lines:
            return None
        if line < 1 or line > len(lines):
            return None

        line_text = lines[line - 1]
        idx = max(col - 1, 0)
        if idx >= len(line_text):
            return None

        while idx < len(line_text) and line_text[idx].isspace():
            idx += 1

        m = re.match(r'[A-Za-z_][A-Za-z0-9_]*', line_text[idx:])
        return m.group(0) if m else None

    def _is_direct_callee_call(self, cursor, file_path: str, expected_name: str) -> bool:
        loc = cursor.location
        if not loc or not loc.file:
            return False

        loc_file = os.path.normpath(str(loc.file))
        cur_file = os.path.normpath(file_path)
        if loc_file != cur_file:
            return False

        ident = self._get_identifier_at_location(file_path, loc.line, loc.column)
        return ident == expected_name

    def _find_first_named_descendant(self, cursor) -> Optional[str]:
        spelling = cursor.spelling
        if spelling:
            return spelling

        for child in cursor.get_children():
            nested = self._find_first_named_descendant(child)
            if nested:
                return nested

        return None

    def _resolve_call_name(self, cursor, file_path: str) -> str:
        if cursor.spelling:
            return cursor.spelling

        loc = cursor.location
        if loc and loc.file:
            loc_file = os.path.normpath(str(loc.file))
            cur_file = os.path.normpath(file_path)
            if loc_file == cur_file:
                ident = self._get_identifier_at_location(file_path, loc.line, loc.column)
                if ident:
                    return ident

        children = list(cursor.get_children())
        if children:
            return self._find_first_named_descendant(children[0]) or ''

        return ''

    def _collect_targets(
        self,
        call_args: Sequence[Any],
        file_path: str,
        ctx_id: str,
        rule: CallRule,
    ) -> List[Tuple[Any, str]]:
        targets: List[Tuple[Any, str]] = []

        for lit_idx, lit in enumerate(self.get_string_literals(call_args[rule.text_arg_index], file_path=file_path)):
            targets.append((lit, f"{ctx_id}:en:{lit_idx}"))

        if rule.include_extra_args:
            for arg_idx, arg in enumerate(call_args[rule.text_arg_index + 1:], start=rule.text_arg_index + 1):
                for lit_idx, lit in enumerate(self.get_string_literals(arg, file_path=file_path)):
                    targets.append((lit, f"{ctx_id}:arg:{arg_idx}:{lit_idx}"))

        return targets

    def _build_replacements_for_call(
        self,
        call_cursor,
        ctx_id: str,
        file_path: str,
        rule: CallRule,
    ) -> List[Tuple[int, int, bytes]]:
        if rule.require_direct_callee and not self._is_direct_callee_call(call_cursor, file_path, call_cursor.spelling):
            return []

        args = list(call_cursor.get_arguments())
        if len(args) <= rule.text_arg_index:
            return []

        targets = self._collect_targets(args, file_path, ctx_id, rule)

        replacements: List[Tuple[int, int, bytes]] = []
        for lit, key in targets:
            start = lit.extent.start.offset
            end = lit.extent.end.offset
            original_src = lit.translation_unit.spelling  # placeholder to satisfy type checkers
            # Read original text from offsets to preserve exact escaping/prefixes.
            original_src = self._current_content_bytes[start:end]
            new_src = f'{self.translator_func}("{key}", '.encode("utf-8") + original_src + b")"
            replacements.append((start, end, new_src))

        return replacements

    def _scan_cursor(self, cursor, rel_path: str, file_path: str, edits: List[Tuple[int, int, bytes]]) -> None:
        if cursor.kind == clang.cindex.CursorKind.CALL_EXPR:
            callee = self._resolve_call_name(cursor, file_path).lower()
            rule = CALL_RULES.get(callee)
            if rule:
                line = int(cursor.location.line or 0)
                col = int(cursor.location.column or 0)
                key = (line, col, rule.bucket)
                ctx_id = self.ctx_by_file.get(rel_path, {}).get(key)
                if ctx_id:
                    edits.extend(self._build_replacements_for_call(cursor, ctx_id, file_path, rule))

        for child in cursor.get_children():
            self._scan_cursor(child, rel_path, file_path, edits)

    def process_file(self, file_path: str, dry_run: bool = False) -> int:        
        if not self.should_process_file(file_path):
            return 0

        rel_path = self._norm_rel_path(os.path.relpath(file_path, self.project_root))
        if rel_path not in self.ctx_by_file:
            return 0

        with open(file_path, "rb") as f:
            content_bytes = f.read()
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            self._file_lines[file_path] = f.readlines()

        self._current_content_bytes = content_bytes
        self._file_bytes[file_path] = content_bytes
        tu = self.index.parse(file_path, args=self.clang_args)
        edits: List[Tuple[int, int, bytes]] = []
        self._scan_cursor(tu.cursor, rel_path, file_path, edits)

        if not edits:
            return 0

        new_content = content_bytes
        for start, end, repl in sorted(edits, key=lambda x: x[0], reverse=True):
            new_content = new_content[:start] + repl + new_content[end:]

        if not dry_run:
            with open(file_path, "wb") as f:
                f.write(new_content)

        return len(edits)

    def run(self, src_dir: str, dry_run: bool = False) -> None:
        total_files = 0
        total_replacements = 0

        for root, _, files in os.walk(src_dir):
            for name in files:
                if not name.endswith(".c"):
                    continue
                total_files += 1
                full = os.path.join(root, name)
                replaced = self.process_file(full, dry_run=dry_run)
                if replaced:
                    rel = os.path.relpath(full, self.project_root)
                    print(f"{rel}: replaced {replaced} string literal(s)")
                    total_replacements += replaced

        print(f"Scanned {total_files} file(s), replaced {total_replacements} string literal(s).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Replace string literals at mapped context_id call sites with translator calls: "
            "translator(\"<ctx_id>:en\", original_literal), or for pline extra args "
            "translator(\"<ctx_id>:arg:<idx>\", original_literal)."
        )
    )
    parser.add_argument("--project-root", default=os.getcwd()+"/Nethack", help="Project root directory")
    parser.add_argument("--src-dir", default="src", help="Directory to scan recursively for .c files")
    parser.add_argument("--db", default="nethack_strings.json", help="Path to merged strings JSON")
    parser.add_argument("--translator", default="tr", help="Translator function name")
    parser.add_argument("--dry-run", action="store_true", help="Only report changes, do not write files")
    args = parser.parse_args()

    src_dir = args.src_dir
    if not os.path.isabs(src_dir):
        src_dir = os.path.join(args.project_root, src_dir)

    injector = TranslationInjector(
        project_root=args.project_root,
        db_path=args.db,
        translator_func=args.translator,
        clang_args=list(PARSE_ARGS),
    )
    injector.run(src_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
