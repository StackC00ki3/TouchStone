import re
import argparse

import clang.cindex
import hashlib
import json
import os
from collections import defaultdict

# 如果你的 libclang 不在标准路径，取消注释下面这行
clang.cindex.Config.set_library_file(r'D:\Scoop\apps\llvm\22.1.1\bin\libclang.dll')

class NetHackScanner:
    def __init__(self, project_root, lang="en"):
        self.project_root = project_root
        self.lang = lang
        self.index = clang.cindex.Index.create()
        self.db = {"pline": {}, "strcpy": {}, "strcat": {}}
        # 跟踪函数内字符串出现次数: { "filename:funcname:text": count }
        self.occurrence_tracker = defaultdict(int)
        self._file_lines = {}
        self._file_bytes = {}


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
        
    def _is_cursor_spelled_within(self, cursor, scope_node, file_path):
        """Whether cursor text is spelled directly inside scope_node in the same source file."""
        if not cursor or not scope_node:
            return False
        if not cursor.location or not cursor.location.file:
            return False
        if not cursor.extent or not scope_node.extent:
            return False

        cur_file = os.path.normpath(str(cursor.location.file))
        src_file = os.path.normpath(file_path)
        if cur_file != src_file:
            return False

        cur_start = cursor.extent.start.offset
        cur_end = cursor.extent.end.offset
        scope_start = scope_node.extent.start.offset
        scope_end = scope_node.extent.end.offset

        if min(cur_start, cur_end, scope_start, scope_end) < 0:
            return False

        return scope_start <= cur_start and cur_end <= scope_end

    def _get_source_slice_by_extent(self, cursor, file_path):
        """Get raw source slice for cursor extent from current file bytes."""
        buf = self._file_bytes.get(file_path)
        if buf is None or not cursor or not cursor.extent:
            return ""

        start = cursor.extent.start.offset
        end = cursor.extent.end.offset
        if min(start, end) < 0 or end <= start or end > len(buf):
            return ""

        return buf[start:end].decode("utf-8", errors="ignore")

    def _literal_matches_source_at_extent(self, cursor, file_path):
        """Validate that source text at cursor extent really contains this literal."""
        src = self._get_source_slice_by_extent(cursor, file_path)
        if not src or '"' not in src:
            return False

        spelling = (cursor.spelling or "").strip()
        if not spelling:
            return False

        # Direct match covers most regular string literals.
        if spelling in src:
            return True

        # Fallback for prefixed literals like u8"..." where spelling may omit prefix.
        if spelling.startswith('"') and spelling.endswith('"') and spelling in src:
            return True

        return False

    def get_string_literals(self, node, file_path=None):
        """递归查找节点下的所有字符串字面量（按出现顺序）"""
        results = []
        last_offset = -1

        def visit(cur):
            nonlocal last_offset

            cur_offset = -1
            if cur and cur.extent:
                cur_offset = cur.extent.start.offset

            # 遍历要求 offset 严格递增；非递增节点直接跳过
            if cur_offset >= 0:
                if cur_offset < last_offset:
                    return
                last_offset = cur_offset

            if cur.kind == clang.cindex.CursorKind.STRING_LITERAL:
                if file_path:
                    if self._literal_matches_source_at_extent(cur, file_path):
                        # 确认该源码位置确实有这个字符串字面量
                        text = self.decode_octal_utf8(cur.spelling.removeprefix('"').removesuffix('"'))
                        results.append(text)
                    else:
                        pass
                else:
                    text = self.decode_octal_utf8(cur.spelling.removeprefix('"').removesuffix('"'))
                    results.append(text)
            for child in cur.get_children():
                visit(child)

        visit(node)
        return results

    def get_string_literal(self, node):
        """兼容旧逻辑：返回第一个字符串字面量"""
        texts = self.get_string_literals(node)
        return texts[0] if texts else None

    def _get_identifier_at_location(self, file_path, line, col):
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

        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", line_text[idx:])
        return m.group(0) if m else None

    def _is_direct_callee_call(self, cursor, file_path, expected_name):
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

    def scan_file(self, file_path):
        # 模拟编译参数，确保 libclang 能找到头文件
        args = ['-Inethack/include', '-Iinclude', '-DNETHACK', '-DRELEASE']
        tu = self.index.parse(file_path, args=args)
        with open(file_path, 'rb') as f:
            self._file_bytes[file_path] = f.read()
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            self._file_lines[file_path] = f.readlines()
        
        # 记录当前遍历到的函数名
        self._current_func = "global"
        self._current_func_cursor = None
        self._scan_cursor(tu.cursor, file_path)


    def _new_ctx(self, rel_path):
        """Generate incremental occurrence index and stable context id for current file/function."""
        occ_key = f"{rel_path}:{self._current_func}"
        self.occurrence_tracker[occ_key] += 1
        idx = self.occurrence_tracker[occ_key]

        # Context ID: 文件+函数+出现次数
        ctx_seed = f"{occ_key}:{idx}"
        ctx_id = hashlib.md5(ctx_seed.encode("utf-8", errors="ignore")).hexdigest()
        return idx, ctx_id

    
    def get_ast_string(self,node, indent=0):
        lines = []
        
        # 1. 构造当前节点的信息
        # 格式：[种类] 拼写 (显示名)
        node_info = f"{'  ' * indent}[{node.kind.name}] {node.spelling} {node.displayname}".strip()
        # 处理空行或无名节点的情况，确保缩进对其
        lines.append('  ' * indent + node_info.lstrip())
        
        # 2. 递归获取子节点字符串
        for child in node.get_children():
            child_string = self.get_ast_string(child, indent + 1)
            if child_string:
                lines.append(child_string)
                
        return "\n".join(lines)

    def _scan_cursor(self, cursor, file_path):
        # 1. 更新当前函数名
        if cursor.kind == clang.cindex.CursorKind.FUNCTION_DECL:
            self._current_func = cursor.spelling
            self._current_func_cursor = cursor

        # 2. 识别 pline 调用
        if cursor.kind == clang.cindex.CursorKind.CALL_EXPR and cursor.spelling == 'pline':
            if not self._is_direct_callee_call(cursor, file_path, 'pline'):
                for child in cursor.get_children():
                    self._scan_cursor(child, file_path)
                return

            call_args = list(cursor.get_arguments())
            if call_args:
                raw_texts = self.get_string_literals(call_args[0], file_path=file_path)
                if raw_texts:
                    rel_path = os.path.relpath(file_path, self.project_root)
                    idx, ctx_id = self._new_ctx(rel_path)
                    # 提取其他字符串字面量参数
                    other_string_args = []
                    for i, arg in enumerate(call_args[1:], start=1):
                        arg_texts = self.get_string_literals(arg, file_path=file_path)
                        if arg_texts:
                            other_string_args.append({
                                f"idx": i,
                                self.lang: arg_texts,
                            })

                    self.db["pline"][ctx_id] = {
                        "file": rel_path,
                        "line": cursor.location.line,
                        "col": cursor.location.column,
                        "func": self._current_func,
                        "occ": idx,
                        self.lang: raw_texts,
                        "args": other_string_args,
                    }

        elif cursor.kind == clang.cindex.CursorKind.CALL_EXPR and cursor.spelling == 'strcpy':
            call_args = list(cursor.get_arguments())
            if call_args and len(call_args) >= 2:
                raw_texts = self.get_string_literals(call_args[1], file_path=file_path)
                if raw_texts:
                    rel_path = os.path.relpath(file_path, self.project_root)
                    idx, ctx_id = self._new_ctx(rel_path)

                    self.db["strcpy"][ctx_id] = {
                        "file": rel_path,
                        "line": cursor.location.line,
                        "col": cursor.location.column,
                        "func": self._current_func,
                        "occ": idx,
                        self.lang: raw_texts,
                    }

        elif cursor.kind == clang.cindex.CursorKind.CALL_EXPR and cursor.spelling == 'strcat':
            call_args = list(cursor.get_arguments())
            if call_args and len(call_args) >= 2:
                raw_texts = self.get_string_literals(call_args[1], file_path=file_path)
                if raw_texts:
                    rel_path = os.path.relpath(file_path, self.project_root)
                    idx, ctx_id = self._new_ctx(rel_path)

                    self.db["strcat"][ctx_id] = {
                        "file": rel_path,
                        "line": cursor.location.line,
                        "col": cursor.location.column,
                        "func": self._current_func,
                        "occ": idx,
                        self.lang: raw_texts,
                    }
        # 递归遍历子节点
        for child in cursor.get_children():
            self._scan_cursor(child, file_path)

    def save_json(self, output_file):
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.db, f, ensure_ascii=False, indent=2)

# 使用示例
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan NetHack pline strings.")
    parser.add_argument("--lang", choices=["en", "zh"], default="en", help="Language key used in output JSON")
    args = parser.parse_args()

    scanner = NetHackScanner(os.getcwd()+"/Nethack", lang=args.lang)
    target_dir = scanner.project_root + "/src"
    
    for filename in os.listdir(target_dir):
        if filename.endswith(".c"):
            print(f"Scanning {filename}...")
            scanner.scan_file(os.path.join(target_dir, filename))
            
    scanner.save_json("nethack_strings.json")
    print(f"Done! Extracted {len(scanner.db['pline']) + len(scanner.db['strcpy']) + len(scanner.db['strcat'])} strings.")