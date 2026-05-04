#include "mod_api.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(_WIN32) || defined(WIN32)
#include <windows.h>
#include <dbghelp.h>
#endif

#if defined(__unix__) || defined(__APPLE__)
#include <execinfo.h>
#endif

struct tr_static_entry {
    const char *key;
    const char *value;
};

struct tr_category_entry {
    const char *category;
    const char *source;
    const char *value;
};

static const struct tr_static_entry tr_exact_table[] = {
#include "tr_table_exact.inc"
};

static const struct tr_category_entry tr_category_table[] = {
#include "tr_table_category.inc"
};

#define TR_EXACT_COUNT ((int) (sizeof tr_exact_table / sizeof tr_exact_table[0]))
#define TR_CATEGORY_COUNT ((int) (sizeof tr_category_table / sizeof tr_category_table[0]))

struct tr_key_info {
    const char *category;
    const char *source_file;
    const char *function_name;
    const char *callsite_index;
    const char *text_kind;
    const char *variant_index;
};

static FILE *tr_log_fp = (FILE *) 0;
static unsigned long tr_log_seq = 0;

static FILE *tr_open_log(void);
static void tr_write_timestamp(FILE *);
static void tr_write_escaped(FILE *, const char *, const char *);
static void tr_parse_key(const char *, struct tr_key_info *, char *, size_t);
static void tr_log_stacktrace(FILE *);
static void tr_log_success(const char *, const char *, const char *);
static const char *tr_find_exact_value(const char *);
static const char *tr_find_category_value(const char *, const char *);
#if defined(_WIN32) || defined(WIN32)
static int tr_init_symbols(void);
static void tr_log_stackframe_win(FILE *, unsigned, void *);
#endif

static FILE *
tr_open_log(void)
{
    static const char *paths[] = {
        "mods/tr_success_log.txt",
        "tr_success_log.txt"
    };
    size_t i;

    if (tr_log_fp)
        return tr_log_fp;

    for (i = 0; i < sizeof paths / sizeof paths[0]; ++i) {
        tr_log_fp = fopen(paths[i], "a");
        if (tr_log_fp) {
            (void) setvbuf(tr_log_fp, (char *) 0, _IOLBF, 0);
            fprintf(tr_log_fp,
                    "# tr_plugin success log started; path=%s\n",
                    paths[i]);
            fflush(tr_log_fp);
            break;
        }
    }

    return tr_log_fp;
}

static void
tr_write_timestamp(FILE *fp)
{
    time_t now;
    struct tm tm_buf;
    struct tm *tm_ptr = (struct tm *) 0;

    if (!fp)
        return;

    now = time((time_t *) 0);
#if defined(_WIN32) || defined(WIN32)
    if (localtime_s(&tm_buf, &now) == 0)
        tm_ptr = &tm_buf;
#else
    tm_ptr = localtime_r(&now, &tm_buf);
#endif

    if (tm_ptr)
        fprintf(fp, "timestamp=%04d-%02d-%02d %02d:%02d:%02d\n",
                tm_ptr->tm_year + 1900, tm_ptr->tm_mon + 1,
                tm_ptr->tm_mday, tm_ptr->tm_hour, tm_ptr->tm_min,
                tm_ptr->tm_sec);
}

static void
tr_write_escaped(FILE *fp, const char *label, const char *text)
{
    const unsigned char *p;

    if (!fp || !label)
        return;

    fprintf(fp, "%s=\"", label);
    if (!text) {
        fputs("(null)", fp);
    } else {
        for (p = (const unsigned char *) text; *p; ++p) {
            switch (*p) {
            case '\\':
                fputs("\\\\", fp);
                break;
            case '\"':
                fputs("\\\"", fp);
                break;
            case '\n':
                fputs("\\n", fp);
                break;
            case '\r':
                fputs("\\r", fp);
                break;
            case '\t':
                fputs("\\t", fp);
                break;
            default:
                fputc((int) *p, fp);
                break;
            }
        }
    }
    fputs("\"\n", fp);
}

static void
tr_parse_key(const char *key, struct tr_key_info *info, char *buf, size_t bufsz)
{
    char *fields[6];
    char *p;
    int idx;

    if (!info)
        return;

    info->category = (const char *) 0;
    info->source_file = (const char *) 0;
    info->function_name = (const char *) 0;
    info->callsite_index = (const char *) 0;
    info->text_kind = (const char *) 0;
    info->variant_index = (const char *) 0;

    if (!buf || bufsz == 0 || !key || !*key)
        return;

    strncpy(buf, key, bufsz - 1);
    buf[bufsz - 1] = '\0';

    p = buf;
    for (idx = 0; idx < 6; ++idx) {
        fields[idx] = p;
        if (!p)
            break;
        p = strchr(p, ':');
        if (!p)
            break;
        *p++ = '\0';
    }

    info->category = fields[0];
    if (idx >= 1)
        info->source_file = fields[1];
    if (idx >= 2)
        info->function_name = fields[2];
    if (idx >= 3)
        info->callsite_index = fields[3];
    if (idx >= 4)
        info->text_kind = fields[4];
    if (idx >= 5)
        info->variant_index = fields[5];
}

static void
tr_log_stacktrace(FILE *fp)
{
    if (!fp)
        return;

#if defined(_WIN32) || defined(WIN32)
    {
        void *frames[24];
        USHORT depth, i;

        depth = CaptureStackBackTrace(1, (DWORD) (sizeof frames
                                                  / sizeof frames[0]),
                                      frames, (PDWORD) 0);
        fprintf(fp, "stacktrace_depth=%u\n", (unsigned) depth);
        for (i = 0; i < depth; ++i)
            tr_log_stackframe_win(fp, (unsigned) i, frames[i]);
    }
#elif defined(__unix__) || defined(__APPLE__)
    {
        void *frames[24];
        char **symbols;
        int depth, i;

        depth = backtrace(frames, (int) (sizeof frames / sizeof frames[0]));
        fprintf(fp, "stacktrace_depth=%d\n", depth);
        symbols = backtrace_symbols(frames, depth);
        for (i = 1; i < depth; ++i) {
            if (symbols && symbols[i]) {
                fprintf(fp, "stacktrace[%d]=%s\n", i - 1, symbols[i]);
            } else {
                fprintf(fp, "stacktrace[%d]=%p\n", i - 1, frames[i]);
            }
        }
        if (symbols)
            free(symbols);
    }
#else
    fputs("stacktrace_depth=0\n", fp);
#endif
}

#if defined(_WIN32) || defined(WIN32)
static int
tr_init_symbols(void)
{
    static int initialized = 0;
    static int init_attempted = 0;
    HANDLE process;

    if (init_attempted)
        return initialized;

    init_attempted = 1;
    process = GetCurrentProcess();
    SymSetOptions(SYMOPT_DEFERRED_LOADS | SYMOPT_UNDNAME | SYMOPT_LOAD_LINES);
    initialized = SymInitialize(process, (PCSTR) 0, TRUE) ? 1 : 0;
    return initialized;
}

static void
tr_log_stackframe_win(FILE *fp, unsigned index, void *addr)
{
    HANDLE process;
    DWORD64 displacement64 = 0;
    DWORD displacement32 = 0;
    DWORD64 address64;
    char symbol_buf[sizeof(SYMBOL_INFO) + 256];
    PSYMBOL_INFO symbol;
    IMAGEHLP_LINE64 line;

    if (!fp) {
        return;
    }

    if (!tr_init_symbols()) {
        fprintf(fp, "stacktrace[%u]=%p\n", index, addr);
        return;
    }

    process = GetCurrentProcess();
    address64 = (DWORD64) (ULONG_PTR) addr;
    symbol = (PSYMBOL_INFO) symbol_buf;
    memset(symbol_buf, 0, sizeof symbol_buf);
    symbol->SizeOfStruct = sizeof(SYMBOL_INFO);
    symbol->MaxNameLen = 255;

    if (SymFromAddr(process, address64, &displacement64, symbol)) {
        fprintf(fp, "stacktrace[%u]=%s+0x%llx [%p]",
                index, symbol->Name,
                (unsigned long long) displacement64, addr);
    } else {
        fprintf(fp, "stacktrace[%u]=%p", index, addr);
    }

    memset(&line, 0, sizeof line);
    line.SizeOfStruct = sizeof line;
    if (SymGetLineFromAddr64(process, address64, &displacement32, &line)) {
        fprintf(fp, " @ %s:%lu+0x%lx",
                line.FileName ? line.FileName : "?",
                (unsigned long) line.LineNumber,
                (unsigned long) displacement32);
    }

    fputc('\n', fp);
}
#endif

static void
tr_log_success(const char *key, const char *fallback, const char *translated)
{
    FILE *fp;
    char keybuf[512];
    struct tr_key_info info;

    fp = tr_open_log();
    if (!fp)
        return;

    tr_parse_key(key, &info, keybuf, sizeof keybuf);

    fprintf(fp, "----- tr_success #%lu -----\n", ++tr_log_seq);
    tr_write_timestamp(fp);
    tr_write_escaped(fp, "key", key);
    tr_write_escaped(fp, "fallback", fallback);
    tr_write_escaped(fp, "translated", translated);

    if (info.category)
        fprintf(fp, "category=%s\n", info.category);
    if (info.source_file)
        fprintf(fp, "source_file=%s\n", info.source_file);
    if (info.function_name)
        fprintf(fp, "function=%s\n", info.function_name);
    if (info.callsite_index)
        fprintf(fp, "callsite_index=%s\n", info.callsite_index);
    if (info.text_kind)
        fprintf(fp, "text_kind=%s\n", info.text_kind);
    if (info.variant_index)
        fprintf(fp, "variant_index=%s\n", info.variant_index);

    tr_log_stacktrace(fp);
    fputc('\n', fp);
    fflush(fp);
}

static const char *
tr_find_exact_value(const char *key)
{
    int lo = 0, hi = TR_EXACT_COUNT - 1;

    if (!key || !*key)
        return (const char *) 0;

    while (lo <= hi) {
        int mid = lo + ((hi - lo) / 2);
        int cmp = strcmp(key, tr_exact_table[mid].key);

        if (cmp == 0) {
            return tr_exact_table[mid].value;
        }
        if (cmp < 0)
            hi = mid - 1;
        else
            lo = mid + 1;
    }

    return (const char *) 0;
}

static const char *
tr_find_category_value(const char *category, const char *source)
{
    int lo = 0, hi = TR_CATEGORY_COUNT - 1;

    if (!category || !*category || !source)
        return (const char *) 0;

    while (lo <= hi) {
        int mid = lo + ((hi - lo) / 2);
        int cat_cmp = strcmp(category, tr_category_table[mid].category);
        int cmp = cat_cmp;

        if (cat_cmp == 0)
            cmp = strcmp(source, tr_category_table[mid].source);

        if (cmp == 0)
            return tr_category_table[mid].value;
        if (cmp < 0)
            hi = mid - 1;
        else
            lo = mid + 1;
    }

    return (const char *) 0;
}

static const char *
tr_lookup(const char *key, const char *fallback)
{
    const char *translated;

    if (!key || !*key)
        return fallback;

    if (strchr(key, ':'))
        translated = tr_find_exact_value(key);
    else
        translated = tr_find_category_value(key, fallback);

    if (translated) {
        tr_log_success(key, fallback, translated);
        return translated;
    }

    return fallback;
}

int
nh_mod_init(const struct nh_mod_host_api *api)
{
    if (!api || api->api_version != NH_MOD_API_VERSION || !api->set_tr_hook)
        return -1;

    return api->set_tr_hook(tr_lookup);
}
