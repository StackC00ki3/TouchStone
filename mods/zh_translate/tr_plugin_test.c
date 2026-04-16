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
    return "haha";
}

int
nh_mod_init(const struct nh_mod_host_api *api)
{
    if (!api || api->api_version != NH_MOD_API_VERSION || !api->set_tr_hook)
        return -1;

    return api->set_tr_hook(tr_lookup);
}
