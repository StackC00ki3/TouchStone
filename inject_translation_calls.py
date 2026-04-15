import argparse
import json
import os
import re
from typing import Dict, List, Tuple

import clang.cindex

# If libclang is not in PATH, uncomment and adjust this line.
clang.cindex.Config.set_library_file(r"D:\Scoop\apps\llvm\22.1.1\bin\libclang.dll")


SECTION_TO_CALLEE = {
    "pline": "pline",
    "Strcpy": "strcpy",
    "Strcat": "strcat",
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

        self.ctx_by_file: Dict[str, Dict[Tuple[int, int, str], str]] = {}
        self._load_ctx_index()

    @staticmethod
    def _norm_rel_path(path: str) -> str:
        return os.path.normpath(path)

    def _load_ctx_index(self) -> None:
        with open(self.db_path, "r", encoding="utf-8") as f:
            db = json.load(f)

        for section, callee in SECTION_TO_CALLEE.items():
            sec = db.get(section, {})
            for ctx_id, entry in sec.items():
                rel_file = self._norm_rel_path(entry["file"])
                line = int(entry["line"])
                col = int(entry["col"])
                key = (line, col, callee)
                per_file = self.ctx_by_file.setdefault(rel_file, {})
                per_file[key] = ctx_id

    @staticmethod
    def _iter_subtree(cursor):
        yield cursor
        for child in cursor.get_children():
            yield from TranslationInjector._iter_subtree(child)


    def _is_inside_translator_call(self, node) -> bool:
        p = node.semantic_parent
        while p is not None:
            if p.kind == clang.cindex.CursorKind.CALL_EXPR and p.spelling == self.translator_func:
                return True
            p = p.semantic_parent
        return False

    def decode_octal_utf8(self, text):
        """
        将 C 风格的八进制转义字符串 (\350\246\201) 转换为 UTF-8 中文
        """
        # 匹配所有的 \xxx 格式 (x 是 0-7)
        def replace_octal(match):
            # 将八进制字符串转为整数，再转为字节
            octal_str = match.group(1)
            return bytes([int(octal_str, 8)])

        try:
            # 1. 先把所有 \xxx 替换为对应的原始字节流
            # 注意：这里需要处理连续的八进制串
            byte_data = re.sub(r'\\\\([0-7]{1,3})', replace_octal, text)
            
            # 2. 将字节流按 UTF-8 解码 (NetHack 源码通常是 UTF-8 或 Latin1)
            # 如果是字符串字面量提取出来的，它本身可能是 bytes 类型
            if isinstance(byte_data, str):
                # 处理已经被转义成字面量 '\\' 的情况
                byte_data = byte_data.encode('latin1').decode('unicode_escape').encode('latin1')
                
            return byte_data.decode('utf-8')
        except Exception as e:
            print(f"Warning: Failed to decode octal UTF-8 in text '{text}'. Error: {e}")
            # 如果解码失败（比如不是合法的 UTF-8），返回原串避免丢失数据
            return text
        
    def get_string_literals(self, node):
        """递归查找节点下的所有字符串字面量（按出现顺序）"""
        results = []

        def visit(cur):
            if cur.kind == clang.cindex.CursorKind.STRING_LITERAL:
                # Return cursor nodes so callers can use extent offsets for source replacement.
                results.append(cur)
            for child in cur.get_children():
                visit(child)

        visit(node)
        return results

    def _build_replacements_for_call(self, call_cursor, ctx_id: str) -> List[Tuple[int, int, bytes]]:
        args = list(call_cursor.get_arguments())
        if not args:
            return []

        targets: List[Tuple[object, str]] = []
        callee = call_cursor.spelling.lower()

        if callee == "pline":
            for arg_idx, lit in enumerate(self.get_string_literals(args[0])):
                targets.append((lit, f"{ctx_id}:en:{arg_idx}"))
            for arg_idx, arg in enumerate(args[1:], start=1):
                for lit_idx, lit in enumerate(self.get_string_literals(arg)):
                    targets.append((lit, f"{ctx_id}:arg:{arg_idx}:{lit_idx}"))
        elif callee in ("strcpy", "strcat"):
            if len(args) >= 2:
                for arg_idx, lit in enumerate(self.get_string_literals(args[1])):
                    targets.append((lit, f"{ctx_id}:en:{arg_idx}"))

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

    def _scan_cursor(self, cursor, rel_path: str, edits: List[Tuple[int, int, bytes]]) -> None:
        if cursor.kind == clang.cindex.CursorKind.CALL_EXPR:
            callee = cursor.spelling.lower()
            if callee in ("pline", "strcpy", "strcat"):
                line = int(cursor.location.line or 0)
                col = int(cursor.location.column or 0)
                key = (line, col, callee)
                ctx_id = self.ctx_by_file.get(rel_path, {}).get(key)
                if ctx_id:
                    edits.extend(self._build_replacements_for_call(cursor, ctx_id))

        for child in cursor.get_children():
            self._scan_cursor(child, rel_path, edits)

    def process_file(self, file_path: str, dry_run: bool = False) -> int:
        rel_path = self._norm_rel_path(os.path.relpath(file_path, self.project_root))
        if rel_path not in self.ctx_by_file:
            return 0

        tu = self.index.parse(file_path, args=self.clang_args)

        with open(file_path, "rb") as f:
            content_bytes = f.read()

        self._current_content_bytes = content_bytes
        edits: List[Tuple[int, int, bytes]] = []
        self._scan_cursor(tu.cursor, rel_path, edits)

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
        clang_args=["-Iinclude", "-Inethack/include", "-DNETHACK", "-DRELEASE"],
    )
    injector.run(src_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
