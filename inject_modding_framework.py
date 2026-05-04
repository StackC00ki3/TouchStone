#!/usr/bin/env python3
"""
Inject Modding Framework - Orchestrate modding framework injection into Nethack.

Usage:
    python inject_modding_framework.py [--dry-run] [--verbose]
"""

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


@dataclass(frozen=True)
class CommandStep:
    title: str
    cmd: tuple[str, ...]
    cwd: Optional[Path] = None
    failure_message: Optional[str] = None


@dataclass(frozen=True)
class CopyStep:
    title: str
    src: Path
    dst: Path
    failure_message: Optional[str] = None


Step = CommandStep | CopyStep


def format_command(cmd: Sequence[str]) -> str:
    """Format a subprocess command for readable logging."""
    return subprocess.list2cmdline(list(cmd))


def print_step(index: int, title: str) -> None:
    print("\n" + "=" * 30)
    print(f"Step {index}: {title}")
    print("=" * 30)


def run_command(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    verbose: bool = False,
    dry_run: bool = False,
) -> int:
    """Run a command and return its exit code."""
    cmd_str = format_command(cmd)
    if verbose or dry_run:
        print(f"Running: {cmd_str}")
        if cwd:
            print(f"  cwd: {cwd}")

    if dry_run:
        print(f"[DRY-RUN] Would execute: {cmd_str}")
        return 0

    try:
        result = subprocess.run(cmd, cwd=cwd, check=False)
    except OSError as exc:
        print(f"Error: Failed to run command: {exc}")
        return 1

    if result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        print(f"Command: {cmd_str}")

    return result.returncode


def copy_file(src: Path, dst: Path, *, verbose: bool = False, dry_run: bool = False) -> bool:
    """Copy a file from src to dst."""
    if not src.exists():
        print(f"Error: Source file not found: {src}")
        return False

    if verbose or dry_run:
        print(f"Copying: {src} -> {dst}")

    if dry_run:
        print(f"[DRY-RUN] Would copy: {src} -> {dst}")
        return True

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        print(f"Error: Failed to copy file: {exc}")
        return False

    if verbose:
        print(f"Successfully copied: {dst}")
    return True


def build_steps(script_dir: Path, *, verbose: bool = False) -> list[Step]:
    nethack_dir = script_dir / "Nethack"
    patches_dir = script_dir / "patches"

    patch_names = (
        "mod_framework.patch",
        "pm_name.patch",
        "objnam.patch",
        "pline_prefix.patch",
        "tty_utf8_v6.patch",
        "win32_utf8.patch",
    )

    steps: list[Step] = [
        CommandStep(
            title="Restoring Nethack to clean state...",
            cmd=("git", "restore", "."),
            cwd=nethack_dir,
            failure_message="git restore returned non-zero exit code",
        ),
        CommandStep(
            title="Cleaning untracked files in Nethack...",
            cmd=("git", "clean", "-fd"),
            cwd=nethack_dir,
            failure_message="git clean returned non-zero exit code",
        ),
        CommandStep(
            title="Injecting translation calls...",
            cmd=(sys.executable, str(script_dir / "inject_translation_calls.py")),
            cwd=script_dir,
            failure_message="inject_translation_calls.py returned non-zero exit code",
        ),
    ]

    for patch_name in patch_names:
        cmd = [
            sys.executable,
            str(script_dir / "patcher.py"),
            str(patches_dir / patch_name),
            "--base-dir",
            str(nethack_dir),
        ]
        if verbose:
            cmd.append("--verbose")

        steps.append(
            CommandStep(
                title=f"Applying {patch_name}...",
                cmd=tuple(cmd),
                cwd=script_dir,
                failure_message=f"{patch_name} apply failed",
            )
        )

    steps.append(
        CopyStep(
            title="Copying mod_api.h to Nethack\\include...",
            src=patches_dir / "mod_api.h",
            dst=nethack_dir / "include" / "mod_api.h",
            failure_message="Could not copy mod_api.h",
        )
    )

    return steps


def execute_step(step: Step, *, verbose: bool = False, dry_run: bool = False) -> bool:
    if isinstance(step, CommandStep):
        ret = run_command(step.cmd, cwd=step.cwd, verbose=verbose, dry_run=dry_run)
        if ret != 0:
            print(step.failure_message or "Command step failed")
            return False
        return True

    success = copy_file(step.src, step.dst, verbose=verbose, dry_run=dry_run)
    if not success:
        print(step.failure_message or "Copy step failed")
    return success


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
    
    script_dir = Path(__file__).parent.resolve()

    if args.verbose:
        print(f"Working directory: {script_dir}")

    steps = build_steps(script_dir, verbose=args.verbose)
    for index, step in enumerate(steps):
        print_step(index, step.title)
        if not execute_step(step, verbose=args.verbose, dry_run=args.dry_run):
            print(f"Step {index} failed")
            return 1

    print("\n" + "=" * 30)
    print("All steps completed successfully!")
    print("=" * 30)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
