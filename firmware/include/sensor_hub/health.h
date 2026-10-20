#ifndef SENSOR_HUB_HEALTH_H
#define SENSOR_HUB_HEALTH_H

#include <stdbool.h>
#include <stdint.h>

#include "sensor_hub/types.h"

typedef struct {
    uint32_t last_heartbeat_ms[HUB_TASK_COUNT];
    uint32_t deadline_ms[HUB_TASK_COUNT];
    uint8_t seen_mask;
} HubHealthMonitor;

void hub_health_init(HubHealthMonitor *monitor);
void hub_health_heartbeat(HubHealthMonitor *monitor, HubTaskId task,
                          uint32_t now_ms);
bool hub_health_all_tasks_alive(const HubHealthMonitor *monitor,
                                uint32_t now_ms, uint32_t *miss_count);

#endif
