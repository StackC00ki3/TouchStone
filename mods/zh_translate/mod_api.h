/* NetHack mod plugin host API */
#ifndef MOD_API_H
#define MOD_API_H

#ifdef __cplusplus
extern "C" {
#endif

#define NH_MOD_API_VERSION 1u
#define NH_MOD_INIT_SYMBOL "nh_mod_init"

typedef const char *(*nh_tr_hook_fn)(const char *key, const char *fallback);

struct nh_mod_host_api {
    unsigned api_version;
    int (*set_tr_hook)(nh_tr_hook_fn hook);
};

typedef int (*nh_mod_init_fn)(const struct nh_mod_host_api *api);

#ifdef __cplusplus
}
#endif

#endif /* MOD_API_H */
