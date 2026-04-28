from __future__ import annotations

import re
from typing import Dict, List, Optional

import clang.cindex

_LITERAL_PREFIXES = ("u8", "u", "U", "L")


def decode_octal_utf8(text: str) -> str:
    """Decode C-style octal escapes to UTF-8 text when possible."""
    if "\\" not in text:
        return text

    try:
        normalized = re.sub(
            r"\\([0-7]{1,3})",
            lambda m: f"\\x{int(m.group(1), 8):02x}",
            text,
        )
        decoded = normalized.encode("latin1", errors="backslashreplace").decode("unicode_escape")
    except Exception as e:
        print(f"Warning: Failed to decode octal UTF-8 in text '{text}'. Error: {e}")
        return text

    try:
        return decoded.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return decoded


def get_source_slice_by_extent(file_bytes: Dict[str, bytes], cursor, file_path: str) -> str:
    """Get raw source slice for cursor extent from the cached file bytes."""
    buf = file_bytes.get(file_path)
    if buf is None or not cursor or not cursor.extent:
        return ""

    start = cursor.extent.start.offset
    end = cursor.extent.end.offset
    if min(start, end) < 0 or end <= start or end > len(buf):
        return ""

    return buf[start:end].decode("utf-8", errors="ignore")


def _strip_literal_prefix(text: str) -> str:
    text = text.strip()
    for prefix in _LITERAL_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].lstrip()
    return text


def _strip_literal_quotes(text: str) -> str:
    text = _strip_literal_prefix(text)
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def literal_matches_source_at_extent(file_bytes: Dict[str, bytes], cursor, file_path: str) -> bool:
    """Validate that source text at cursor extent really contains this literal."""
    src = get_source_slice_by_extent(file_bytes, cursor, file_path)
    if not src or '"' not in src:
        return False

    spelling = (cursor.spelling or "").strip()
    if not spelling:
        return False

    if spelling in src:
        return True

    if spelling.startswith('"') and spelling.endswith('"') and any(
        f"{prefix}{spelling}" in src for prefix in _LITERAL_PREFIXES
    ):
        return True

    decoded_spelling = decode_octal_utf8(_strip_literal_quotes(spelling))
    decoded_src = decode_octal_utf8(_strip_literal_quotes(src))
    return decoded_spelling == decoded_src


def get_string_literal_cursors(node, file_bytes: Dict[str, bytes], file_path: Optional[str] = None) -> List[object]:
    """Collect string literal cursors in appearance order under a node."""
    results: List[object] = []
    last_offset = -1

    def visit(cur) -> None:
        nonlocal last_offset

        cur_offset = -1
        if cur and cur.extent:
            cur_offset = cur.extent.start.offset

        if cur_offset >= 0:
            if cur_offset < last_offset:
                return
            last_offset = cur_offset

        if cur.kind == clang.cindex.CursorKind.STRING_LITERAL:
            if not file_path or literal_matches_source_at_extent(file_bytes, cur, file_path):
                results.append(cur)

        for child in cur.get_children():
            visit(child)

    visit(node)
    return results


def _cursor_to_literal_text(cursor) -> str:
    spelling = (cursor.spelling or "").strip()
    return decode_octal_utf8(_strip_literal_quotes(spelling))


def get_string_literal_texts(node, file_bytes: Dict[str, bytes], file_path: Optional[str] = None) -> List[str]:
    """Collect decoded string literal texts in appearance order under a node."""
    cursors = get_string_literal_cursors(node, file_bytes=file_bytes, file_path=file_path)
    return [_cursor_to_literal_text(cur) for cur in cursors]
