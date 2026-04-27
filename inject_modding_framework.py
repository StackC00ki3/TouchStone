#!/usr/bin/env python3
"""
Inject Modding Framework - Orchestrate modding framework injection into Nethack

This script performs the following steps in order:
1. Run inject_translation_calls.py to inject translation call hooks
2. Apply the mod_framework.patch to add modding API support
3. Copy mod_api.h to the Nethack include directory

Usage:
    python inject_modding_framework.py [--dry-run] [--verbose]
"""

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path


def run_command(cmd, verbose=False, dry_run=False,cwd=None):
    """Run a command and return its exit code"""
    cmd_str = " ".join(cmd)
    if verbose or dry_run:
        print(f"Running: {cmd_str}")
    
    if dry_run:
        print(f"[DRY-RUN] Would execute: {cmd_str}")
        return 0
    
    try:
        result = subprocess.run(cmd, check=True, shell=True, cwd=cwd)
        return result.returncode
    except subprocess.CalledProcessError as e:
        print(f"Error: Command failed with exit code {e.returncode}")
        print(f"Command: {cmd_str}")
        return e.returncode
    except Exception as e:
        print(f"Error: Failed to run command: {e}")
        return 1


def copy_file(src, dst, verbose=False, dry_run=False):
    """Copy a file from src to dst"""
    src_path = Path(src)
    dst_path = Path(dst)
    
    if not src_path.exists():
        print(f"Error: Source file not found: {src}")
        return False
    
    # Create destination directory if it doesn't exist
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    
    if verbose or dry_run:
        print(f"Copying: {src} -> {dst}")
    
    if dry_run:
        print(f"[DRY-RUN] Would copy: {src} -> {dst}")
        return True
    
    try:
        shutil.copy2(src_path, dst_path)
        if verbose:
            print(f"Successfully copied: {dst}")
        return True
    except Exception as e:
        print(f"Error: Failed to copy file: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrate modding framework injection into Nethack"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    # Get the script directory
    script_dir = Path(__file__).parent.resolve()
    os.chdir(script_dir)
    
    if args.verbose:
        print(f"Working directory: {script_dir}")
    
    print("\n" + "=" * 30)
    print("Step 0: Restoring Nethack to clean state...")
    print("=" * 30)
    ret = run_command(
        ["git", "restore", "."],
        verbose=args.verbose,
        dry_run=args.dry_run,
        cwd=Path(script_dir).joinpath("Nethack")
    )
    if ret != 0 and not args.dry_run:
        print("Step 0 failed: git restore returned non-zero exit code")
        return 1
    
    ret = run_command(
        ["git", "clean", "-fd"],
        verbose=args.verbose,
        dry_run=args.dry_run,
        cwd=Path(script_dir).joinpath("Nethack")
    )

    if ret != 0 and not args.dry_run:
        print("Step 0 failed: git clean returned non-zero exit code")
        return 1

    # Step 1: Run inject_translation_calls.py
    print("\n" + "=" * 30)
    print("Step 1: Injecting translation calls...")
    print("=" * 30)
    
    ret = run_command(
        [sys.executable, "inject_translation_calls.py"],
        verbose=args.verbose,
        dry_run=args.dry_run
    )
    
    if ret != 0 and not args.dry_run:
        print("Step 1 failed: inject_translation_calls.py returned non-zero exit code")
        return 1
    
    # Step 2: Apply mod_framework.patch
    print("\n" + "=" * 30)
    print("Step 2: Applying mod_framework.patch...")
    print("=" * 30)
    
    cmd = [
        sys.executable,
        "patcher.py",
        ".\\patches\\mod_framework.patch",
        "--base-dir", ".\\Nethack\\"
    ]
    if args.verbose:
        cmd.append("-v")
    
    ret = run_command(
        cmd,
        verbose=args.verbose,
        dry_run=args.dry_run
    )
    
    if ret != 0 and not args.dry_run:
        print("Step 2 failed: patcher.py returned non-zero exit code")
        return 1
    
    # Step 3: Apply tty_utf8_v4.patch
    print("\n" + "=" * 30)
    print("Step 3: Applying tty_utf8_v4.patch...")
    print("=" * 30)
    
    cmd = [
        sys.executable,
        "patcher.py",
        ".\\patches\\tty_utf8_v4.patch",
        "--base-dir", ".\\Nethack\\"
    ]
    if args.verbose:
        cmd.append("-v")
    
    ret = run_command(
        cmd,
        verbose=args.verbose,
        dry_run=args.dry_run
    )
    
    if ret != 0 and not args.dry_run:
        print("Step 3 failed: patcher.py returned non-zero exit code")
        return 1
    
    # Step 4: Apply win32_utf8.patch
    print("\n" + "=" * 30)
    print("Step 4: Applying win32_utf8.patch...")
    print("=" * 30)
    
    cmd = [
        sys.executable,
        "patcher.py",
        ".\\patches\\win32_utf8.patch",
        "--base-dir", ".\\Nethack\\"
    ]
    if args.verbose:
        cmd.append("-v")
    
    ret = run_command(
        cmd,
        verbose=args.verbose,
        dry_run=args.dry_run
    )
    
    if ret != 0 and not args.dry_run:
        print("Step 4 failed: patcher.py returned non-zero exit code")
        return 1


    # Step 5: Copy mod_api.h to Nethack\include
    print("\n" + "=" * 30)
    print("Step 5: Copying mod_api.h to Nethack\\include...")
    print("=" * 30)
    
    src_file = "patches\\mod_api.h"
    dst_file = "Nethack\\include\\mod_api.h"
    
    if not copy_file(src_file, dst_file, verbose=args.verbose, dry_run=args.dry_run):
        print("Step 5 failed: Could not copy mod_api.h")
        return 1
    
    print("\n" + "=" * 30)
    print("✓ All steps completed successfully!")
    print("=" * 30)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
