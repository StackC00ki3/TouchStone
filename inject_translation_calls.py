import argparse
import json
import os
from typing import Dict, List, Tuple

from tree_sitter import Language, Node, Parser
import tree_sitter_c


CALLEES = [
    "pline",
    "strcpy",
    "strcat",
    "Strcpy",
    "Strcat",
]

class TranslationInjector:
    def __init__(
        self,
        project_root: str,
        db_path: str,
        translator_func: str,
    ):
        self.project_root = os.path.abspath(project_root)
        self.db_path = db_path
        self.translator_func = translator_func
        self._current_content_bytes = b""
        self.parser = Parser()
        self._set_c_language()

        self.ctx_by_file: Dict[str, Dict[Tuple[int, int, str], str]] = {}
        self._load_ctx_index()

    def _set_c_language(self) -> None:
        lang_obj = tree_sitter_c.language()
        if isinstance(lang_obj, Language):
            c_lang = lang_obj
        else:
            c_lang = Language(lang_obj)

        if hasattr(self.parser, "set_language"):
            self.parser.set_language(c_lang)
        else:
            self.parser.language = c_lang

    @staticmethod
    def _norm_rel_path(path: str) -> str:
        return os.path.normpath(path)

    def _load_ctx_index(self) -> None:
        with open(self.db_path, "r", encoding="utf-8") as f:
            db = json.load(f)

        for callee in CALLEES:
            sec = db.get(callee, {})
            for ctx_id, entry in sec.items():
                rel_file = self._norm_rel_path(entry["file"])
                line = int(entry["line"])
                col = int(entry["col"])
                key = (line, col, callee)
                per_file = self.ctx_by_file.setdefault(rel_file, {})
                per_file[key] = ctx_id

    @staticmethod
    def _iter_subtree(node: Node):
        yield node
        for child in node.children:
            yield from TranslationInjector._iter_subtree(child)

    def _node_text(self, node: Node) -> str:
        return self._current_content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _extract_callee_name(self, func_node: Node) -> str:
        if func_node.type == "identifier":
            return self._node_text(func_node)
        if func_node.type == "parenthesized_expression":
            for child in func_node.named_children:
                return self._extract_callee_name(child)
        return ""

    def _is_inside_translator_call(self, node: Node) -> bool:
        p = node.parent
        while p is not None:
            if p.type == "call_expression":
                func_node = p.child_by_field_name("function")
                if func_node and self._extract_callee_name(func_node) == self.translator_func:
                    return True
            p = p.parent
        return False

    def _string_key(self, node: Node) -> str:
        # Deduplicate by the literal source text in this expression subtree.
        return self._node_text(node)

    def _collect_string_nodes(self, node: Node) -> List[Node]:
        result: List[Node] = []

        for cur in self._iter_subtree(node):
            if cur.type not in ("string_literal", "concatenated_string"):
                continue
            if self._is_inside_translator_call(cur):
                continue
            result.append(cur)

        return result

    def _get_call_arguments(self, call_node: Node) -> List[Node]:
        args_node = call_node.child_by_field_name("arguments")
        if not args_node:
            return []
        return [child for child in args_node.named_children if child.type != "comment"]

    def _build_replacements_for_call(self, call_node: Node, callee: str, ctx_id: str) -> List[Tuple[int, int, bytes]]:
        args = self._get_call_arguments(call_node)
        if not args:
            return []

        targets: List[Tuple[Node, str]] = []

        if callee == "pline":
            for alt_idx, lit in enumerate(self._collect_string_nodes(args[0])):
                targets.append((lit, f"{ctx_id}:en:{alt_idx}"))
            for arg_idx, arg in enumerate(args[1:], start=1):
                for alt_idx, lit in enumerate(self._collect_string_nodes(arg)):
                    targets.append((lit, f"{ctx_id}:arg:{arg_idx}:{alt_idx}"))
        elif callee in ("strcpy", "strcat", "Strcpy", "Strcat"):
            if len(args) >= 2:
                for alt_idx, lit in enumerate(self._collect_string_nodes(args[1])):
                    targets.append((lit, f"{ctx_id}:en:{alt_idx}"))

        replacements: List[Tuple[int, int, bytes]] = []
        for lit, key in targets:
            start = lit.start_byte
            end = lit.end_byte
            # Read original text from offsets to preserve exact escaping/prefixes.
            original_src = self._current_content_bytes[start:end]
            new_src = f'{self.translator_func}("{key}", '.encode("utf-8") + original_src + b")"
            replacements.append((start, end, new_src))

        return replacements

    def _scan_cursor(self, cursor: Node, rel_path: str, edits: List[Tuple[int, int, bytes]]) -> None:
        if cursor.type == "call_expression":
            func_node = cursor.child_by_field_name("function")
            callee = self._extract_callee_name(func_node) if func_node else ""
            if callee in CALLEES:
                row, col0 = cursor.start_point
                line = int(row + 1)
                col = int(col0 + 1)
                key = (line, col, callee)
                ctx_id = self.ctx_by_file.get(rel_path, {}).get(key)
                if ctx_id:
                    edits.extend(self._build_replacements_for_call(cursor, callee, ctx_id))

        for child in cursor.children:
            self._scan_cursor(child, rel_path, edits)

    def process_file(self, file_path: str, dry_run: bool = False) -> int:
        rel_path = self._norm_rel_path(os.path.relpath(file_path, self.project_root))
        if rel_path not in self.ctx_by_file:
            return 0

        with open(file_path, "rb") as f:
            content_bytes = f.read()

        self._current_content_bytes = content_bytes
        tree = self.parser.parse(content_bytes)
        edits: List[Tuple[int, int, bytes]] = []
        self._scan_cursor(tree.root_node, rel_path, edits)

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
    )
    injector.run(src_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
