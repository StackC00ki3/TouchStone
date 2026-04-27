# TouchStone

[中文版 README](README-zh.md)
TouchStone is a modding framework for NetHack.

## Repository Overview

- `scanner.py`: Scans C source code for string literals used in `pline/strcpy/strcat`, then outputs a context database.
- `compare_strings.py`: Flet GUI to compare two `nethack_strings.json` files and export merged results.
- `flet_en_merge_tool.py`: Flet GUI that matches merged entries by English text, lets you manually revise, and saves `zh`.
- `generate_tr_table_inc.py`: Generates a C include translation table from merged JSON.
- `inject_translation_calls.py`: Rewrites target string literals in NetHack sources into `tr("key", original_literal)` calls.
- `patcher.py`: Context-matching patch applier for unified diff patches.
- `inject_modding_framework.py`: Orchestrates the full mod framework injection flow (restore workspace, inject, patch, copy headers).
- `patches/`: Maintained patch files.
- `mods/`: Example plugins and plugin API headers.

## Requirements

Install Python dependencies:

```powershell
pip install clang flet
```

## Important Configuration

`scanner.py` and `inject_translation_calls.py` have a hardcoded `libclang.dll` path.
Update it to match your local environment:

```python
clang.cindex.Config.set_library_file(r"D:\Scoop\apps\llvm\22.1.1\bin\libclang.dll")
```

If the path is wrong, scripts will fail during parsing.

## Typical Workflow

### 1) Scan Strings

Run at the repository root:

```powershell
python scanner.py --lang en
```

Output: `nethack_strings.json`

### 2) Compare and Export Merged Data

Launch the GUI tool:

```powershell
python compare_strings.py
```

In the UI, load A/B file paths and run merge export to generate:

- `nethack_strings_merged.json`

### 3) Manually Review/Fill Translations

```powershell
python flet_en_merge_tool.py
```

This tool saves changes to:

- `nethack_strings_a_modified.json`

### 4) Generate C Translation Table

```powershell
python generate_tr_table_inc.py -i nethack_strings_merged.json -o Nethack/src/tr_table_data.inc
```

### 5) Inject Translation Calls

```powershell
python inject_translation_calls.py --project-root .\Nethack --src-dir src --db nethack_strings.json --translator tr
```

Preview first if needed:

```powershell
python inject_translation_calls.py --dry-run
```

### 6) Apply Patches

```powershell
python patcher.py .\patches\mod_framework.patch --base-dir .\Nethack\
python patcher.py .\patches\tty_utf8_v4.patch --base-dir .\Nethack\
python patcher.py .\patches\win32_utf8.patch --base-dir .\Nethack\
```

Dry-run preview is also supported:

```powershell
python patcher.py .\patches\mod_framework.patch --base-dir .\Nethack\ --dry-run -v
```

### 7) One-Command Flow

```powershell
python inject_modding_framework.py -v
```

This script automatically runs the full mod framework injection pipeline.

## Notes

- Step 1 in `inject_modding_framework.py` runs `git restore .` and `git clean -fd` in the `Nethack` directory.
- This removes uncommitted changes and untracked files. Back up your work first.

## Plugin Development (mods)

`mods/README.md` explains plugin exports, build steps, and platform suffixes.
If you want to compile the example plugin under `mods/zh_translate`, refer to that document directly.