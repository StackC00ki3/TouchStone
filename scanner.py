import argparse
import hashlib
import json
import os
from collections import defaultdict

from tree_sitter import Language, Parser
import tree_sitter_c

class NetHackScanner:
    def __init__(self, project_root, lang="en"):
        self.project_root = project_root
        self.lang = lang
        self.parser = self._build_parser()
        self.db = {"pline": {}, "Strcpy": {}, "Strcat": {}, "strcpy": {}, "strcat": {}}
        # 跟踪函数内字符串出现次数: { "filename:funcname": count }
        self.occurrence_tracker = defaultdict(int)
        self._file_lines = {}

    def _build_parser(self):
        c_lang = Language(tree_sitter_c.language())
        parser = Parser(c_lang)
        return parser

    def _node_text(self, node, source_bytes):
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

    def _extract_identifier(self, node, source_bytes):
        if node is None:
            return None
        if node.type == "identifier":
            return self._node_text(node, source_bytes)
        for child in node.children:
            name = self._extract_identifier(child, source_bytes)
            if name:
                return name
        return None

    def _call_callee_name(self, call_node, source_bytes):
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None
        if func_node.type == "identifier":
            return self._node_text(func_node, source_bytes)
        return self._extract_identifier(func_node, source_bytes)

    def _collect_string_literals(self, node, source_bytes):
        """递归查找节点下的字符串字面量（按出现顺序）。"""
        results = []

        def visit(cur):
            if cur.type == "string_literal":
                raw = self._node_text(cur, source_bytes)
                text = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
                results.append(text)
            for child in cur.children:
                visit(child)

        visit(node)
        return results

    def _iter_named(self, node):
        if node.is_named:
            yield node
        for child in node.children:
            yield from self._iter_named(child)

    def scan_file(self, file_path):
        with open(file_path, "rb") as f:
            source_bytes = f.read()

        source_text = source_bytes.decode("utf-8", errors="ignore")
        self._file_lines[file_path] = source_text.splitlines(keepends=True)

        tree = self.parser.parse(source_bytes)
        rel_path = os.path.relpath(file_path, self.project_root)

        root = tree.root_node
        for node in self._iter_named(root):
            if node.type != "function_definition":
                continue

            decl = node.child_by_field_name("declarator")
            func_name = self._extract_identifier(decl, source_bytes) or "global"
            body = node.child_by_field_name("body")
            if body is None:
                continue

            self._scan_function_body(body, rel_path, func_name, source_bytes)

    def _scan_function_body(self, body_node, rel_path, func_name, source_bytes):
        for node in self._iter_named(body_node):
            if node.type != "call_expression":
                continue

            callee = self._call_callee_name(node, source_bytes)
            if callee not in {"pline", "strcpy", "strcat", "Strcpy", "Strcat"}:
                continue

            arg_list = node.child_by_field_name("arguments")
            if arg_list is None:
                continue

            args = [child for child in arg_list.named_children if child.type != "comment"]
            if callee == "pline":
                self._handle_pline_call(node, args, rel_path, func_name, source_bytes)
            elif callee == "strcpy":
                self._handle_strcpy_or_strcat_call("strcpy", node, args, rel_path, func_name, source_bytes)
            elif callee == "strcat":
                self._handle_strcpy_or_strcat_call("strcat", node, args, rel_path, func_name, source_bytes)
            elif callee == "Strcpy":
                self._handle_strcpy_or_strcat_call("Strcpy", node, args, rel_path, func_name, source_bytes)
            elif callee == "Strcat":
                self._handle_strcpy_or_strcat_call("Strcat", node, args, rel_path, func_name, source_bytes)

    def _new_ctx(self, rel_path, func_name):
        occ_key = f"{rel_path}:{func_name}"
        self.occurrence_tracker[occ_key] += 1
        idx = self.occurrence_tracker[occ_key]
        ctx_seed = f"{occ_key}:{idx}"
        ctx_id = hashlib.md5(ctx_seed.encode("utf-8", errors="ignore")).hexdigest()
        return idx, ctx_id

    def _handle_pline_call(self, call_node, args, rel_path, func_name, source_bytes):
        if not args:
            return

        raw_texts = self._collect_string_literals(args[0], source_bytes)
        if not raw_texts:
            return

        idx, ctx_id = self._new_ctx(rel_path, func_name)
        other_string_args = []
        for i, arg in enumerate(args[1:], start=1):
            arg_texts = self._collect_string_literals(arg, source_bytes)
            if arg_texts:
                other_string_args.append({
                    "idx": i,
                    self.lang: arg_texts,
                })

        self.db["pline"][ctx_id] = {
            "file": rel_path,
            "line": call_node.start_point[0] + 1,
            "col": call_node.start_point[1] + 1,
            "func": func_name,
            "occ": idx,
            self.lang: raw_texts,
            "args": other_string_args,
        }

    def _handle_strcpy_or_strcat_call(self, bucket_name, call_node, args, rel_path, func_name, source_bytes):
        if len(args) < 2:
            return

        raw_texts = self._collect_string_literals(args[1], source_bytes)
        if not raw_texts:
            return

        idx, ctx_id = self._new_ctx(rel_path, func_name)
        self.db[bucket_name][ctx_id] = {
            "file": rel_path,
            "line": call_node.start_point[0] + 1,
            "col": call_node.start_point[1] + 1,
            "func": func_name,
            "occ": idx,
            self.lang: raw_texts,
        }

    def save_json(self, output_file):
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.db, f, ensure_ascii=False, indent=2)

# 使用示例
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan NetHack pline strings.")
    parser.add_argument("--lang", choices=["en", "zh"], default="en", help="Language key used in output JSON")
    args = parser.parse_args()

    scanner = NetHackScanner(os.path.join(os.getcwd(), "Nethack"), lang=args.lang)
    target_dir = os.path.join(scanner.project_root, "src")
    
    for filename in os.listdir(target_dir):
        if filename.endswith(".c"):
            print(f"Scanning {filename}...")
            scanner.scan_file(os.path.join(target_dir, filename))
            
    scanner.save_json("nethack_strings.json")
    print(f"Done! Extracted {len(scanner.db['pline']) + len(scanner.db['strcpy']) + len(scanner.db['strcat'])} strings.")