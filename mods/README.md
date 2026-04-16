# NetHack Mods

At startup, NetHack now scans `mods/` and loads plugin files in this folder.
Supported suffixes:

- Linux and other Unix-like systems: `.so`
- macOS: `.dylib` (and `.so`)
- Windows: `.dll` (and `.so`)

Each plugin must export:

- `int nh_mod_init(const struct nh_mod_host_api *api)`

The host API definition is in `include/mod_api.h`.

## Build the translation plugin

From the `Nethack/` directory:

```bash
gcc -shared -fPIC -Iinclude -o mods/tr_plugin.so mods/tr_plugin.c
```

macOS example:

```bash
clang -dynamiclib -Iinclude -o mods/tr_plugin.dylib mods/tr_plugin.c
```

Windows (MinGW) example:

```bash
gcc -shared -Iinclude -o mods/tr_plugin.dll mods/tr_plugin.c
```

When a translation plugin is present, the core `tr()` function will use it.
