#include "../include/mod_api.h"

#include <string.h>

struct tr_static_entry {
    const char *key;
    const char *value;
};

static const struct tr_static_entry tr_table_data[] = {
#include "../src/tr_table_data.inc"
};

#define TR_TABLE_COUNT ((int) (sizeof tr_table_data / sizeof tr_table_data[0]))

static const char *
tr_lookup(const char *key, const char *fallback)
{
    int lo = 0, hi = TR_TABLE_COUNT - 1;

    if (!key || !*key)
        return fallback;

    while (lo <= hi) {
        int mid = lo + ((hi - lo) / 2);
        int cmp = strcmp(key, tr_table_data[mid].key);

        if (cmp == 0) {
            const char *v = tr_table_data[mid].value;
            return (v && *v) ? v : fallback;
        }
        if (cmp < 0)
            hi = mid - 1;
        else
            lo = mid + 1;
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
