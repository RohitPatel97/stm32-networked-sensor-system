#include "sensor_hub/sensor_state.h"

#include <stddef.h>

#include "sensor_hub/config.h"

void hub_sensor_state_update(bool success, bool *online, uint8_t *failures,
                              HubCounters *counters) {
    if (online == NULL || failures == NULL || counters == NULL) {
        return;
    }
    if (success) {
        *failures = 0U;
        if (!*online) {
            *online = true;
            counters->sensor_reconnects++;
        }
        return;
    }
    if (*failures < UINT8_MAX) {
        (*failures)++;
    }
    if (*online && *failures >= HUB_SENSOR_DISCONNECT_THRESHOLD) {
        *online = false;
        counters->sensor_disconnects++;
    }
}
