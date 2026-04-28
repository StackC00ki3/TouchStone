import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Dict, List, Optional, Sequence, Tuple

import clang.cindex

from clang_string_literals import get_string_literal_texts

clang.cindex.Config.set_library_file(r'D:\Scoop\apps\llvm\22.1.1\bin\libclang.dll')

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
    'You': CallRule('You', text_arg_index=0, include_extra_args=True),
    'Your': CallRule('Your', text_arg_index=0, include_extra_args=True),
    'You_feel': CallRule('You_feel', text_arg_index=0, include_extra_args=True),
    'You_cant': CallRule('You_cant', text_arg_index=0, include_extra_args=True),
    'pline_The': CallRule('pline_The', text_arg_index=0, include_extra_args=True),
    'There': CallRule('There', text_arg_index=0, include_extra_args=True),
    'You_hear': CallRule('You_hear', text_arg_index=0, include_extra_args=True),
    'You_see': CallRule('You_see', text_arg_index=0, include_extra_args=True),
    'strcpy': CallRule('strcpy', text_arg_index=1),
    'strcat': CallRule('strcat', text_arg_index=1),
    'sprintf': CallRule('sprintf', text_arg_index=1, include_extra_args=True),
    'end_menu': CallRule('end_menu', text_arg_index=1),
    'add_menu': CallRule('add_menu', text_arg_index=7),
}

DB_BUCKETS = tuple(dict.fromkeys(rule.bucket for rule in CALL_RULES.values()))


class NetHackScanner:
    def __init__(self, project_root: str, lang: str = 'en'):
        self.project_root = project_root
        self.lang = lang
        self.index = clang.cindex.Index.create()
        self.db: Dict[str, Dict[str, Dict[str, Any]]] = {bucket: {} for bucket in DB_BUCKETS}
        # 跟踪函数内字符串出现次数: { "filename:funcname": count }
        self.occurrence_tracker: DefaultDict[str, int] = defaultdict(int)
        self._file_lines: Dict[str, List[str]] = {}
        self._file_bytes: Dict[str, bytes] = {}
        self._current_func = 'global'

    @staticmethod
    def should_process_file(file_path: str) -> bool:
        """判断是否需要处理此文件"""
        filename = os.path.basename(file_path)
        return filename not in SKIP_FILES

    def get_string_literals(self, node, file_path: Optional[str] = None) -> List[str]:
        """递归查找节点下的所有字符串字面量（按出现顺序）"""
        return get_string_literal_texts(node, file_bytes=self._file_bytes, file_path=file_path)

    def get_string_literal(self, node) -> Optional[str]:
        """兼容旧逻辑：返回第一个字符串字面量"""
        texts = self.get_string_literals(node)
        return texts[0] if texts else None

    def _get_identifier_at_location(self, file_path: str, line: int, col: int) -> Optional[str]:
        """Return identifier text at a source location, or None if unavailable."""
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
        """Only keep calls whose source text callee matches expected_name exactly.

        This filters out macro wrappers such as pline1(cstr) -> pline("%s", cstr).
        """
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

    def _load_file_cache(self, file_path: str) -> None:
        with open(file_path, 'rb') as f:
            self._file_bytes[file_path] = f.read()
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            self._file_lines[file_path] = f.readlines()

    def scan_file(self, file_path: str) -> None:
        # 模拟编译参数，确保 libclang 能找到头文件
        tu = self.index.parse(file_path, args=list(PARSE_ARGS))
        self._load_file_cache(file_path)

        # 记录当前遍历到的函数名
        self._current_func = 'global'
        self._scan_cursor(tu.cursor, file_path)

    def _new_ctx(self, rel_path: str, func_name: str) -> Tuple[int, str]:
        """Generate incremental occurrence index and stable context id for current file/function."""
        occ_key = f"{rel_path}:{self._current_func}"
        self.occurrence_tracker[occ_key] += 1
        idx = self.occurrence_tracker[occ_key]

        # Context ID: bucket+文件函数键+出现次数
        ctx_seed = f"{func_name}:{occ_key}:{idx}"
        ctx_id = hashlib.md5(ctx_seed.encode('utf-8', errors='ignore')).hexdigest()
        return idx, ctx_id

    def _collect_extra_string_args(self, call_args: Sequence[Any], start_idx: int, file_path: str) -> List[Dict[str, Any]]:
        other_string_args: List[Dict[str, Any]] = []
        for i, arg in enumerate(call_args[start_idx:], start=start_idx):
            arg_texts = self.get_string_literals(arg, file_path=file_path)
            if arg_texts:
                other_string_args.append({
                    'idx': i,
                    self.lang: arg_texts,
                })
        return other_string_args

    def _record_call(self, cursor, file_path: str, rule: CallRule) -> None:
        if rule.require_direct_callee and not self._is_direct_callee_call(cursor, file_path, cursor.spelling):
            return

        call_args = list(cursor.get_arguments())
        if len(call_args) <= rule.text_arg_index:
            return

        raw_texts = self.get_string_literals(call_args[rule.text_arg_index], file_path=file_path)
        if not raw_texts:
            return

        rel_path = os.path.relpath(file_path, self.project_root)
        idx, ctx_id = self._new_ctx(rel_path, rule.bucket)
        entry: Dict[str, Any] = {
            'file': rel_path,
            'line': cursor.location.line,
            'col': cursor.location.column,
            'func': self._current_func,
            'occ': idx,
            self.lang: raw_texts,
        }

        if rule.include_extra_args:
            entry['args'] = self._collect_extra_string_args(call_args, rule.text_arg_index + 1, file_path)

        self.db[rule.bucket][ctx_id] = entry

    def _scan_cursor(self, cursor, file_path: str) -> None:
        # 1. 更新当前函数名
        if cursor.kind == clang.cindex.CursorKind.FUNCTION_DECL:
            self._current_func = cursor.spelling

        # 2. 识别调用
        if cursor.kind == clang.cindex.CursorKind.CALL_EXPR:
            callee = self._resolve_call_name(cursor, file_path).lower()
            rule = CALL_RULES.get(callee)
            if rule:
                self._record_call(cursor, file_path, rule)

        # 递归遍历子节点
        for child in cursor.get_children():
            self._scan_cursor(child, file_path)

    def save_json(self, output_file: str) -> None:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.db, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description='Scan NetHack pline strings.')
    parser.add_argument('--lang', choices=['en', 'zh'], default='en', help='Language key used in output JSON')
    args = parser.parse_args()

    scanner = NetHackScanner(os.path.join(os.getcwd(), 'Nethack'), lang=args.lang)
    target_dir = os.path.join(scanner.project_root, 'src')

    for filename in sorted(os.listdir(target_dir)):
        if not filename.endswith('.c'):
            continue

        full_path = os.path.join(target_dir, filename)
        print(f'Scanning {filename}...')
        if scanner.should_process_file(full_path):
            scanner.scan_file(full_path)

    scanner.save_json('nethack_strings.json')
    total = sum(len(scanner.db[bucket]) for bucket in DB_BUCKETS)
    print(f'Done! Extracted {total} strings.')


if __name__ == '__main__':
    main()
