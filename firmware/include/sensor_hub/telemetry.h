#ifndef SENSOR_HUB_TELEMETRY_H
#define SENSOR_HUB_TELEMETRY_H

#include <stddef.h>

#include "sensor_hub/types.h"

typedef struct {
    const char *device_id;
    uint64_t boot_id;
    uint32_t frame_sequence;
    uint64_t uptime_ms;
    bool sample_present;
    bool watchdog_enabled;
    uint32_t reset_flags;
} HubTelemetryIdentity;

/* Shared v3 wire format used by the board and compiled simulator. */
int hub_telemetry_format_v3(char *buffer, size_t capacity,
                          const HubProcessedSample *sample, const HubStatus *status,
                          const HubCounters *counters, const HubTelemetryIdentity *identity);

/* Return frame bytes (excluding the terminating NUL), or -1 for invalid
 * arguments, non-finite active payloads, or insufficient capacity. On failure
 * a non-NULL buffer with nonzero capacity is always an empty C string. */
int hub_telemetry_format(char *buffer, size_t capacity,
                         const HubProcessedSample *sample,
                         const HubStatus *status,
                         const HubCounters *counters);

#endif
