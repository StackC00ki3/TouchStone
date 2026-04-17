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


def apply_hunk(file_path: str, hunk: Hunk, dry_run: bool = False, verbose: bool = False) -> Tuple[bool, str]:
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
    estimated_start = max(0, hunk.header.old_start - 1)
    
    # First try: exact location from hunk header
    actual_start = None
    
    # Check if we can match starting from estimated position
    if context_before and old_lines:
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
    for i, hunk in enumerate(file_patch.hunks, 1):
        success, msg = apply_hunk(target_path, hunk, dry_run, verbose)
        messages.append(f"  Hunk {i}: {msg}")
        if not success:
            return False, messages
    
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
#!/usr/bin/env python3
"""
Context-based patch tool: applies git patches using surrounding lines to locate changes.
Useful when hunk header information is unreliable or offsets have changed.
"""

import re
import sys
import os
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass


@dataclass
class Hunk:
    """Represents a single patch hunk"""
    file_path: str
    # Context lines before the change (with optional leading space/+/-)
    pre_context: List[str]
    # Lines to remove (content after '−')
    removed_lines: List[str]
    # Lines to add (content after '+')
    added_lines: List[str]
    # Context lines after the change
    post_context: List[str]


class PatchParser:
    """Parse git patch format"""
    
    def __init__(self, patch_content: str):
        self.lines = patch_content.split('\n')
        self.hunks: List[Hunk] = []
        self.parse()
    
    def parse(self):
        """Parse the patch file into hunks"""
        i = 0
        current_file = None
        
        while i < len(self.lines):
            line = self.lines[i]
            
            # Match: a/path/to/file
            if line.startswith('--- a/'):
                current_file = line[6:]  # Remove '--- a/'
                i += 1
                continue
            
            # Match: +++ b/path/to/file
            if line.startswith('+++ b/'):
                i += 1
                continue
            
            # Match hunk header: @@ -start,count +start,count @@
            if line.startswith('@@'):
                hunk = self._parse_hunk(current_file, i)
                if hunk:
                    self.hunks.append(hunk)
                    i = hunk.end_line if hasattr(hunk, 'end_line') else i + 1
                i += 1
                continue
            
            i += 1
    
    def _parse_hunk(self, file_path: str, start_idx: int) -> Optional[Hunk]:
        """Parse a single hunk starting at the @@ line"""
        hunk_header = self.lines[start_idx]
        
        pre_context = []
        removed_lines = []
        added_lines = []
        post_context = []
        
        i = start_idx + 1
        # Initially we're looking for pre-context
        # After we see a - or +, we're in the main change
        # After seeing a -, if we see a ' ' (context), we're in post-context
        seen_change = False
        
        while i < len(self.lines):
            if i >= len(self.lines):
                break
            
            line = self.lines[i]
            
            # End of hunk - next hunk or file marker
            if line.startswith('@@') or line.startswith('--- a/') or line.startswith('+++ b/') or line.startswith('diff --git'):
                break
            
            # Ignore the line if it's beyond the patch or is a new file marker
            if line.startswith('\\'):  # "\ No newline at end of file"
                i += 1
                continue
            
            # Must have at least one character for prefix
            if not line:
                # Empty lines might be part of content - be careful
                if not seen_change:
                    # Still in pre-context, empty line is a context line
                    pre_context.append('')
                else:
                    # After seeing changes, empty line indicates post-context
                    post_context.append('')
                i += 1
                continue
            
            prefix = line[0]
            content = line[1:] if len(line) > 1 else ''
            
            if prefix == ' ':  # Context line
                if not seen_change:
                    # Still collecting pre-context
                    pre_context.append(content)
                else:
                    # We've already seen changes, now in post-context
                    post_context.append(content)
            
            elif prefix == '-':  # Remove line
                seen_change = True
                removed_lines.append(content)
            
            elif prefix == '+':  # Add line
                seen_change = True
                added_lines.append(content)
            
            i += 1
        
        if not (removed_lines or added_lines):
            return None
        
        hunk = Hunk(
            file_path=file_path,
            pre_context=pre_context,
            removed_lines=removed_lines,
            added_lines=added_lines,
            post_context=post_context
        )
        hunk.end_line = i  # type: ignore
        return hunk


class ContextMatcher:
    """Find patch location based on context lines"""
    
    @staticmethod
    def find_match(file_lines: List[str], hunk: Hunk) -> Optional[int]:
        """
        Find the line number where this hunk should be applied.
        Returns the line number (0-indexed) of the first line to be removed/changed,
        or None if no match found.
        """
        pre = hunk.pre_context
        removed = hunk.removed_lines
        post = hunk.post_context
        
        if not removed and not hunk.added_lines:
            return None
        
        # Try to find a match using context lines
        # Strategy: if no removed lines, just match pre_context + post_context
        # Otherwise: pre_context + removed_lines + post_context
        
        for start_idx in range(len(file_lines)):
            match = True
            
            # Check pre-context (lines before the change)
            for i, ctx_line in enumerate(pre):
                idx = start_idx - len(pre) + i
                if idx < 0:
                    match = False
                    break
                if file_lines[idx] != ctx_line:
                    match = False
                    break
            
            if not match:
                continue
            
            # If no removed lines, just look for post-context after current position
            if not removed:
                # Check post-context
                for i, ctx_line in enumerate(post):
                    if start_idx + i >= len(file_lines):
                        match = False
                        break
                    if file_lines[start_idx + i] != ctx_line:
                        match = False
                        break
                
                if match:
                    return start_idx
                continue
            
            # Check removed lines
            end_idx = start_idx
            for i, rem_line in enumerate(removed):
                if start_idx + i >= len(file_lines):
                    match = False
                    break
                if file_lines[start_idx + i] != rem_line:
                    match = False
                    break
                end_idx = start_idx + i
            
            if not match:
                continue
            
            # Check post-context (lines after the removed section)
            for i, ctx_line in enumerate(post):
                idx = end_idx + 1 + i
                if idx >= len(file_lines):
                    match = False
                    break
                if file_lines[idx] != ctx_line:
                    match = False
                    break
            
            if match:
                return start_idx
        
        return None


class Patcher:
    """Apply patches based on context matching"""
    
    def __init__(self, patch_file: str, verbose: bool = False):
        with open(patch_file, 'r', encoding='utf-8', errors='replace') as f:
            patch_content = f.read()
        self.parser = PatchParser(patch_content)
        self.verbose = verbose
    
    def apply(self, source_dir: str = '.', dry_run: bool = False) -> Dict[str, bool]:
        """
        Apply all hunks in the patch.
        Returns dict mapping file paths to success status.
        """
        results = {}
        
        for hunk_idx, hunk in enumerate(self.parser.hunks):
            file_path = os.path.join(source_dir, hunk.file_path)
            
            if not os.path.exists(file_path):
                print(f"❌ Hunk {hunk_idx}: File not found: {file_path}")
                results[hunk.file_path] = False
                continue
            
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                file_content = f.read()
                # Normalize line endings
                file_lines = file_content.replace('\r\n', '\n').split('\n')
            
            # Find where to apply the hunk
            match_line = ContextMatcher.find_match(file_lines, hunk)
            
            if match_line is None:
                print(f"❌ Hunk {hunk_idx}: Could not find context in: {hunk.file_path}")
                if self.verbose:
                    print(f"   Pre-context ({len(hunk.pre_context)} lines):")
                    for line in hunk.pre_context[:3]:
                        print(f"      | {line[:70]}")
                    print(f"   Removed ({len(hunk.removed_lines)} lines):")
                    for line in hunk.removed_lines[:3]:
                        print(f"      - {line[:70]}")
                    print(f"   Post-context ({len(hunk.post_context)} lines):")
                    for line in hunk.post_context[:3]:
                        print(f"      | {line[:70]}")
                results[hunk.file_path] = False
                continue
            
            # Apply the patch
            removed_count = len(hunk.removed_lines)
            new_lines = (
                file_lines[:match_line] +
                hunk.added_lines +
                file_lines[match_line + removed_count:]
            )
            
            if not dry_run:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(new_lines))
                print(f"✓ Hunk {hunk_idx}: Patched: {hunk.file_path} (line {match_line+1})")
            else:
                print(f"[DRY-RUN] Hunk {hunk_idx}: Would patch: {hunk.file_path} (line {match_line+1})")
            
            if self.verbose:
                print(f"         Removed {removed_count} lines, added {len(hunk.added_lines)} lines")
            
            results[hunk.file_path] = True
        
        return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python patcher.py <patch_file> [options]")
        print("\nOptions:")
        print("  --source-dir <path>  Root directory for patching (default: .)")
        print("  --dry-run            Show what would be patched without modifying files")
        print("  --verbose, -v        Show detailed matching information")
        print("\nApplies git patches using context lines to locate changes.")
        sys.exit(1)
    
    patch_file = sys.argv[1]
    source_dir = '.'
    dry_run = False
    verbose = False
    
    # Parse arguments
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--source-dir' and i + 1 < len(sys.argv):
            source_dir = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--dry-run':
            dry_run = True
            i += 1
        elif sys.argv[i] in ('--verbose', '-v'):
            verbose = True
            i += 1
        else:
            i += 1
    
    if not os.path.exists(patch_file):
        print(f"Error: Patch file not found: {patch_file}")
        sys.exit(1)
    
    patcher = Patcher(patch_file, verbose=verbose)
    results = patcher.apply(source_dir=source_dir, dry_run=dry_run)
    
    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    
    print(f"\n{'='*50}")
    print(f"Results: {success_count}/{total_count} files patched successfully")
    
    if success_count < total_count:
        sys.exit(1)


if __name__ == '__main__':
    main()
