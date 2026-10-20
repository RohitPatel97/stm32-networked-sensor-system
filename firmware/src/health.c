#include "sensor_hub/health.h"

#include <string.h>

void hub_health_init(HubHealthMonitor *monitor) {
    if (monitor == NULL) {
        return;
    }
    memset(monitor, 0, sizeof(*monitor));
    monitor->deadline_ms[HUB_TASK_ACQUISITION] = 30U;
    monitor->deadline_ms[HUB_TASK_PROCESSING] = 100U;
    monitor->deadline_ms[HUB_TASK_TELEMETRY] = 250U;
    monitor->deadline_ms[HUB_TASK_HEALTH] = 1500U;
}

void hub_health_heartbeat(HubHealthMonitor *monitor, HubTaskId task,
                          uint32_t now_ms) {
    if (monitor == NULL || (unsigned int)task >= HUB_TASK_COUNT) {
        return;
    }
    monitor->last_heartbeat_ms[task] = now_ms;
    monitor->seen_mask |= (uint8_t)(1U << (uint8_t)task);
}

bool hub_health_all_tasks_alive(const HubHealthMonitor *monitor,
                                uint32_t now_ms, uint32_t *miss_count) {
    uint32_t misses = 0U;
    unsigned int task;

    if (monitor == NULL) {
        if (miss_count != NULL) {
            *miss_count = HUB_TASK_COUNT;
        }
        return false;
    }
    for (task = 0U; task < HUB_TASK_COUNT; ++task) {
        const uint32_t age = now_ms - monitor->last_heartbeat_ms[task];
        if ((monitor->seen_mask & (1U << task)) == 0U ||
            age > monitor->deadline_ms[task]) {
            misses++;
        }
    }
    if (miss_count != NULL) {
        *miss_count = misses;
    }
    return misses == 0U;
}
