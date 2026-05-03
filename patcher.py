#!/usr/bin/env python3
"""
Git Patch Tool - Apply patches with context-aware line matching

This tool applies patches from git diff format by using the surrounding context
lines to locate where patches should be applied, even if line numbers have changed.

Usage:
    python patcher.py patch_file.patch --base-dir /path/to/project [--dry-run] [--verbose]
    
Features:
    - Parses unified diff format (git diff)
    - Uses context lines to find patch locations (flexible line number matching)
    - Supports --dry-run to preview changes
    - Detailed error reporting with line numbers and mismatches
"""

import os
import sys
import re
import argparse
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass


@dataclass
class HunkHeader:
    """Represent a hunk header from unified diff format"""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    context: str  # e.g., "function_name" if present
    
    @staticmethod
    def parse(line: str) -> Optional['HunkHeader']:
        """Parse a hunk header like: @@ -88,20 +88,24 @@ optional context"""
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', line)
        if not match:
            return None
        return HunkHeader(
            old_start=int(match.group(1)),
            old_count=int(match.group(2) or 1),
            new_start=int(match.group(3)),
            new_count=int(match.group(4) or 1),
            context=match.group(5).strip()
        )


@dataclass
class Hunk:
    """Represent a single patch hunk"""
    header: HunkHeader
    lines: List[Tuple[str, str]]  # (line_type, content) where line_type is ' ', '+', '-'
    
    def get_context_before(self) -> List[str]:
        """Get context lines before any changes"""
        before = []
        for line_type, content in self.lines:
            if line_type == ' ':
                before.append(content)
            else:
                break
        return before
    
    def get_context_after(self) -> List[str]:
        """Get context lines after all changes"""
        after = []
        for line_type, content in reversed(self.lines):
            if line_type == ' ':
                after.insert(0, content)
            else:
                break
        return after


@dataclass
class FilePatch:
    """Represent a patch for a single file"""
    old_path: str
    new_path: str
    hunks: List[Hunk]


def parse_patch(patch_content: str) -> List[FilePatch]:
    """Parse a unified diff format patch into FilePatch objects"""
    lines = patch_content.split('\n')
    file_patches = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Look for diff header
        if line.startswith('diff --git'):
            match = re.match(r'diff --git a/(.*) b/(.*)', line)
            if not match:
                i += 1
                continue
            
            old_path = match.group(1)
            new_path = match.group(2)
            i += 1
            
            # Skip "index" line if present
            if i < len(lines) and lines[i].startswith('index'):
                i += 1
            
            # Parse --- and +++ lines
            if i < len(lines) and lines[i].startswith('---'):
                i += 1
            if i < len(lines) and lines[i].startswith('+++'):
                i += 1
            
            hunks = []
            
            # Parse hunks
            while i < len(lines):
                if lines[i].startswith('@@'):
                    header = HunkHeader.parse(lines[i])
                    if not header:
                        break
                    i += 1
                    
                    hunk_lines = []
                    # Collect hunk lines
                    while i < len(lines):
                        if lines[i].startswith('@@'):
                            break
                        if lines[i].startswith('diff --git'):
                            break
                        
                        if lines[i].startswith(' '):
                            hunk_lines.append((' ', lines[i][1:]))
                            i += 1
                        elif lines[i].startswith('+'):
                            hunk_lines.append(('+', lines[i][1:]))
                            i += 1
                        elif lines[i].startswith('-'):
                            hunk_lines.append(('-', lines[i][1:]))
                            i += 1
                        elif lines[i].startswith('\\'):
                            # "\ No newline at end of file" marker
                            i += 1
                        else:
                            break
                    
                    hunks.append(Hunk(header, hunk_lines))
                else:
                    break
            
            if hunks:
                file_patches.append(FilePatch(old_path, new_path, hunks))
        else:
            i += 1
    
    return file_patches


def find_context_lines_in_file(file_path: str, context_lines: List[str], start_line: int = 0) -> Optional[int]:
    """
    Find where context_lines appear in file_path starting from start_line.
    Returns the line number (0-indexed) where the context starts, or None if not found.
    """
    if not context_lines:
        return start_line
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            file_lines = f.readlines()
    except Exception:
        return None
    
    # Strip newlines from file_lines for comparison
    file_lines = [line.rstrip('\n\r') for line in file_lines]
    context_lines_stripped = [line.rstrip('\n\r') for line in context_lines]
    
    context_len = len(context_lines_stripped)
    
    if context_len == 0:
        return start_line
    
    # Search starting from start_line, allow some flexibility 
    search_start = max(0, start_line - 5)  # Look a few lines before estimated position
    search_end = min(len(file_lines), start_line + 20)  # Look a bit ahead too
    
    for i in range(search_start, search_end - context_len + 1):
        if file_lines[i:i+context_len] == context_lines_stripped:
            return i
    
    # If not found nearby, search the whole file
    for i in range(0, len(file_lines) - context_len + 1):
        if file_lines[i:i+context_len] == context_lines_stripped:
            return i
    
    return None


def apply_hunk(file_path: str, hunk: Hunk, dry_run: bool = False,
               verbose: bool = False,
               estimated_start_override: Optional[int] = None) -> Tuple[bool, str]:
    """
    Apply a single hunk to a file.
    Returns (success, message)
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            file_content = f.read()
    except Exception as e:
        return False, f"Cannot read file: {e}"
    
    file_lines = file_content.split('\n')
    # Remove trailing empty line if file ends with newline
    if file_lines and file_lines[-1] == '':
        file_lines.pop()
    
    # Get the old and new lines to apply
    old_lines = []
    new_lines = []
    
    for line_type, content in hunk.lines:
        if line_type == '-':
            old_lines.append(content)
        elif line_type == '+':
            new_lines.append(content)
        elif line_type == ' ':
            old_lines.append(content)
            new_lines.append(content)
    
    # Find context lines (unchanged lines)
    context_before = hunk.get_context_before()
    context_after = hunk.get_context_after()
    
    # Try to find where these lines occur in the file
    estimated_start = max(
        0,
        estimated_start_override
        if estimated_start_override is not None
        else hunk.header.old_start - 1
    )
    
    # First try: exact match of the full old hunk text near the estimated
    # position. This is much more reliable than matching only the leading
    # context lines when multiple functions share a similar trailer such as
    # a closing brace and a blank line.
    actual_start = None
    old_len = len(old_lines)

    if old_lines and estimated_start + old_len <= len(file_lines):
        if file_lines[estimated_start:estimated_start + old_len] == old_lines:
            actual_start = estimated_start

    # If not found exactly, search nearby for the full old hunk text.
    if actual_start is None and old_lines:
        search_start = max(0, estimated_start - 10)
        search_end = min(len(file_lines) - old_len + 1, estimated_start + 21)
        for i in range(search_start, search_end):
            if file_lines[i:i + old_len] == old_lines:
                actual_start = i
                break

    # If still not found, search the whole file for the full old hunk text.
    if actual_start is None and old_lines:
        for i in range(0, len(file_lines) - old_len + 1):
            if file_lines[i:i + old_len] == old_lines:
                actual_start = i
                break

    # Fall back to matching leading context if needed.
    if actual_start is None and context_before:
        context_len = len(context_before)
        if estimated_start >= 0 and estimated_start + context_len <= len(file_lines):
            if file_lines[estimated_start:estimated_start + context_len] == context_before:
                actual_start = estimated_start

    # If not found exactly, search nearby
    if actual_start is None and context_before:
        for i in range(max(0, estimated_start - 10), min(len(file_lines), estimated_start + 20)):
            context_len = len(context_before)
            if i + context_len <= len(file_lines):
                if file_lines[i:i + context_len] == context_before:
                    actual_start = i
                    break
    
    # If still not found and we have few context lines, search whole file
    if actual_start is None and context_before and len(context_before) > 0:
        for i in range(len(file_lines) - len(context_before) + 1):
            if file_lines[i:i + len(context_before)] == context_before:
                actual_start = i
                break
    
    if actual_start is None:
        if not context_before and not context_after:
            # No context lines at all, use estimated position
            actual_start = estimated_start
        else:
            return False, f"Cannot find context lines for hunk at line {hunk.header.old_start}"
    
    # Calculate line ranges
    hunk_start = actual_start
    hunk_end = hunk_start + len(old_lines)
    
    # Check bounds
    if hunk_end > len(file_lines):
        return False, f"Hunk extends beyond file length (lines {hunk_start + 1}-{hunk_end}, but file has {len(file_lines)} lines)"
    
    # Verify old lines match
    actual_old_lines = file_lines[hunk_start:hunk_end]
    if actual_old_lines != old_lines:
        # Show mismatch details
        mismatch_lines = []
        for i, (expected, actual) in enumerate(zip(old_lines, actual_old_lines)):
            if expected != actual:
                mismatch_lines.append(f"    Line {hunk_start + i + 1}: expected '{expected}', got '{actual}'")
        detail = '\n'.join(mismatch_lines[:3])
        return False, f"Line mismatch at line {hunk_start + 1}:\n{detail}"
    
    if dry_run:
        msg = f"Would apply hunk at line {hunk_start + 1}:\n"
        msg += f"  Remove {len(old_lines)} lines, add {len(new_lines)} lines"
        
        if verbose:
            # Show what lines are being removed
            removed_count = sum(1 for _, content in hunk.lines if _[0] == '-')
            added_count = sum(1 for _, content in hunk.lines if _[0] == '+')
            
            if removed_count > 0:
                msg += f"\n  Removed lines:"
                for line_type, content in hunk.lines[:5]:
                    if line_type == '-':
                        # Show first 80 chars
                        preview = content[:77] + "..." if len(content) > 80 else content
                        msg += f"\n    - {preview}"
                if removed_count > 5:
                    msg += f"\n    ... and {removed_count - 5} more"
            
            if added_count > 0:
                msg += f"\n  Added lines:"
                for line_type, content in hunk.lines:
                    if line_type == '+':
                        # Show first 80 chars
                        preview = content[:77] + "..." if len(content) > 80 else content
                        msg += f"\n    + {preview}"
        
        return True, msg
    
    # Apply the patch
    new_file_lines = file_lines[:hunk_start] + new_lines + file_lines[hunk_end:]
    
    try:
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            for i, line in enumerate(new_file_lines):
                f.write(line)
                if i < len(new_file_lines) - 1:
                    f.write('\n')
            # Add final newline if original file had it
            if file_content.endswith('\n'):
                f.write('\n')
        return True, f"Applied hunk at line {hunk_start + 1}"
    except Exception as e:
        return False, f"Cannot write file: {e}"


def apply_file_patch(base_dir: str, file_patch: FilePatch, dry_run: bool = False, verbose: bool = False) -> Tuple[bool, List[str]]:
    """
    Apply a FilePatch to the target file.
    Returns (success, [messages])
    """
    # Determine target file path
    target_path = os.path.join(base_dir, file_patch.new_path)
    
    messages = [f"Patching: {file_patch.new_path}"]
    
    if not os.path.exists(target_path):
        messages.append(f"ERROR: File not found: {target_path}")
        return False, messages
    
    # Apply each hunk
    line_offset = 0

    for i, hunk in enumerate(file_patch.hunks, 1):
        estimated_start = max(0, hunk.header.old_start - 1 + line_offset)
        success, msg = apply_hunk(target_path, hunk, dry_run, verbose,
                                  estimated_start_override=estimated_start)
        messages.append(f"  Hunk {i}: {msg}")
        if not success:
            return False, messages
        line_offset += hunk.header.new_count - hunk.header.old_count

    return True, messages


def main():
    parser = argparse.ArgumentParser(
        description='Apply git patches with context-aware line matching'
    )
    parser.add_argument('patch_file', help='Path to the patch file')
    parser.add_argument('--base-dir', '-C', default='.', help='Base directory for patching (default: current directory)')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying them')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output (show what will be added/removed)')
    
    args = parser.parse_args()
    
    # Read patch file
    try:
        with open(args.patch_file, 'r', encoding='utf-8') as f:
            patch_content = f.read()
    except Exception as e:
        print(f"ERROR: Cannot read patch file: {e}", file=sys.stderr)
        return 1
    
    # Parse patch
    file_patches = parse_patch(patch_content)
    
    if not file_patches:
        print("ERROR: No patches found in file", file=sys.stderr)
        return 1
    
    if args.verbose:
        print(f"Found {len(file_patches)} file(s) to patch")
    
    # Apply patches
    all_success = True
    for file_patch in file_patches:
        success, messages = apply_file_patch(args.base_dir, file_patch, args.dry_run, args.verbose)
        
        for msg in messages:
            if msg.startswith('ERROR'):
                print(msg, file=sys.stderr)
                all_success = False
            else:
                print(msg)
    
    if args.dry_run:
        print("\n(dry-run mode - no files modified)")
    
    return 0 if all_success else 1


if __name__ == '__main__':
    sys.exit(main())
