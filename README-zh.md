# TouchStone

TouchStone 是一个面向 NetHack 的模组框架


## 目录概览

- `scanner.py`：扫描 C 源码中的 `pline/strcpy/strcat` 字符串字面量，输出上下文数据库
- `compare_strings.py`：Flet 图形化界面，对比两份 `nethack_strings.json` 并导出合并结果
- `flet_en_merge_tool.py`：Flet 图形化界面，按英文文本匹配 merged 数据，手工修订并保存 `zh`
- `generate_tr_table_inc.py`：把 merged JSON 生成 C include 翻译表
- `inject_translation_calls.py`：将 Nethack 源码中的目标字符串字面量改写为 `tr("key", original_literal)` 形式
- `patcher.py`：上下文匹配式补丁应用器（统一 diff）
- `inject_modding_framework.py`：串联模组框架注入流程（恢复工作区、注入、打补丁、复制头文件）
- `patches/`：维护的补丁文件
- `mods/`：示例插件与插件 API 头文件

## 环境要求

安装 Python 依赖：

```powershell
pip install clang flet
```

## 重要配置

`scanner.py` 与 `inject_translation_calls.py` 中写死了 `libclang.dll` 路径。请按本机环境修改：

```python
clang.cindex.Config.set_library_file(r"D:\Scoop\apps\llvm\22.1.1\bin\libclang.dll")
```

如果路径不正确，脚本会在解析阶段失败。

## 典型工作流

### 1) 扫描字符串

在仓库根目录执行：

```powershell
python scanner.py --lang en
```

输出：`nethack_strings.json`

### 2) 对比并导出 merged

启动图形工具：

```powershell
python compare_strings.py
```

在界面中加载 A/B 路径并执行“合并导出”，生成：

- `nethack_strings_merged.json`

### 3) 手工校对/补齐翻译

```powershell
python flet_en_merge_tool.py
```

该工具会把修改保存到：

- `nethack_strings_a_modified.json`

### 4) 生成 C 翻译表

```powershell
python generate_tr_table_inc.py -i nethack_strings_merged.json -o Nethack/src/tr_table_data.inc
```

### 5) 注入翻译调用

```powershell
python inject_translation_calls.py --project-root .\Nethack --src-dir src --db nethack_strings.json --translator tr
```

可先预览：

```powershell
python inject_translation_calls.py --dry-run
```

### 6) 应用补丁

```powershell
python patcher.py .\patches\mod_framework.patch --base-dir .\Nethack\
python patcher.py .\patches\tty_utf8_v4.patch --base-dir .\Nethack\
python patcher.py .\patches\win32_utf8.patch --base-dir .\Nethack\
```

支持预览：

```powershell
python patcher.py .\patches\mod_framework.patch --base-dir .\Nethack\ --dry-run -v
```

### 7) 一键流程

```powershell
python inject_modding_framework.py -v
```

该脚本会自动执行模组框架注入流程。

## 注意事项

- `inject_modding_framework.py` 第一步会在 `Nethack` 目录执行 `git restore .` 与 `git clean -fd`。
- 这会清理未提交改动与未跟踪文件，请先备份你的工作。


## 插件开发（mods）

`mods/README.md` 提供了插件导出函数、构建方式与平台后缀说明。若你要编译 `mods/zh_translate` 下的示例插件，请直接参考该文件。