#ifndef HUB_IDENTITY_H
#define HUB_IDENTITY_H
#include <stdint.h>
extern char hub_device_id[64];
extern uint64_t hub_boot_id;
void hub_identity_init(void);
#endif
