#ifndef SENSOR_HUB_SENSOR_STATE_H
#define SENSOR_HUB_SENSOR_STATE_H

#include <stdbool.h>
#include <stdint.h>

#include "sensor_hub/types.h"

/* Record one completed acquisition, not each low-level I2C retry. The caller
   owns synchronization; the function performs no I/O or allocation. Counters
   count state transitions, so a prolonged outage is one disconnect. */
void hub_sensor_state_update(bool success, bool *online, uint8_t *failures,
                              HubCounters *counters);

#endif
