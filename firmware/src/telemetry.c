#include "sensor_hub/telemetry.h"
#include "sensor_hub/config.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>

typedef struct {
    char *buffer;
    size_t capacity;
    size_t used;
} FrameWriter;

static bool append(FrameWriter *writer, const char *format, ...) {
    int count;
    const size_t remaining = writer->capacity - writer->used;
    va_list arguments;
    va_start(arguments, format);
    count = vsnprintf(writer->buffer + writer->used, remaining, format, arguments);
    va_end(arguments);
    if (count < 0 || (size_t)count >= remaining) { return false; }
    writer->used += (size_t)count;
    return true;
}

static bool append_u64(FrameWriter *writer, uint64_t value) {
    /* newlib-nano's printf does not support long long, even with _printf_float.
       Convert explicitly so the host and Cortex-M emit identical JSON integers. */
    char digits[20];
    size_t count = 0U;
    do {
        digits[count++] = (char)('0' + value % 10U);
        value /= 10U;
    } while (value != 0U);
    if (count >= writer->capacity - writer->used) { return false; }
    while (count > 0U) { writer->buffer[writer->used++] = digits[--count]; }
    writer->buffer[writer->used] = '\0';
    return true;
}

static bool finite_payloads(const HubProcessedSample *sample) {
    unsigned int axis;
    if ((sample->valid_mask & HUB_MPU6050_VALID) != 0U) {
        for (axis = 0U; axis < 3U; ++axis) {
            if (!isfinite(sample->accel_g[axis]) || !isfinite(sample->gyro_dps[axis])) {
                return false;
            }
        }
        if (!isfinite(sample->imu_temperature_c)) { return false; }
    }
    return (sample->valid_mask & HUB_BMP280_VALID) == 0U ||
           (isfinite(sample->baro_temperature_c) && isfinite(sample->pressure_pa) &&
            isfinite(sample->altitude_m));
}

static bool valid_device_id(const char *id) {
    size_t n = 0U;
    if (id == NULL) { return false; }
    while (id[n] != '\0' && n < 64U) {
        const char c = id[n++];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) { return false; }
    }
    return n > 0U && n <= 64U && id[n] == '\0';
}

static int format_frame(char *buffer, size_t capacity,
                         const HubProcessedSample *sample,
                         const HubStatus *status,
                         const HubCounters *counters, const HubTelemetryIdentity *identity) {
    FrameWriter writer = {buffer, capacity, 0U};
    if (buffer == NULL || capacity == 0U) { return -1; }
    buffer[0] = '\0';
    if (sample == NULL || status == NULL || counters == NULL || !finite_payloads(sample)) {
        return -1;
    }

    /* Write into the caller's bounded buffer, avoiding 352 bytes of temporary
       payload arrays and their final copies into the frame. */
    if (identity != NULL) {
        unsigned int axis;
        if (!valid_device_id(identity->device_id) ||
            (sample->timestamp_us / 1000ULL > identity->uptime_ms ||
             (sample->timestamp_us / 1000ULL == identity->uptime_ms &&
              sample->timestamp_us % 1000ULL != 0ULL)) ||
            (!identity->sample_present && (sample->valid_mask != 0U ||
             sample->sequence != 0U || sample->timestamp_us != 0U))) { goto failed; }
        if ((sample->valid_mask & HUB_BMP280_VALID) != 0U &&
            (sample->pressure_pa < 30000.0f || sample->pressure_pa > 120000.0f ||
             sample->baro_temperature_c < -50.0f || sample->baro_temperature_c > 150.0f)) {
            goto failed;
        }
        if ((sample->valid_mask & HUB_MPU6050_VALID) != 0U) {
            for (axis = 0U; axis < 3U; ++axis) {
                if (fabsf(sample->accel_g[axis]) > 16.0f) { goto failed; }
            }
        }
        if (!append(&writer,
                    "{\"schema_version\":3,\"device_id\":\"%s\",\"boot_id\":",
                    identity->device_id) ||
            !append_u64(&writer, identity->boot_id) ||
            !append(&writer, ",\"frame_seq\":%lu,\"uptime_ms\":",
                    (unsigned long)identity->frame_sequence) ||
            !append_u64(&writer, identity->uptime_ms) ||
            !append(&writer, ",\"sample_present\":%s,",
                    identity->sample_present ? "true" : "false")) { goto failed; }
    } else if (!append(&writer, "{\"schema\":1,")) { goto failed; }
    if (!append(&writer,
                "\"type\":\"telemetry\",\"seq\":%lu,\"ts_us\":",
                (unsigned long)sample->sequence) ||
        !append_u64(&writer, sample->timestamp_us) ||
        !append(&writer, ","
                "\"sensors\":{\"mpu6050\":\"%s\",\"bmp280\":\"%s\"},\"imu\":",
                status->mpu6050_online ? "online" : "offline",
                status->bmp280_online ? "online" : "offline")) { goto failed; }
    if ((sample->valid_mask & HUB_MPU6050_VALID) != 0U) {
        if (!append(&writer,
                    "{\"accel_g\":[%.4f,%.4f,%.4f],\"gyro_dps\":[%.3f,%.3f,%.3f],\"temperature_c\":%.2f}",
                    (double)sample->accel_g[0], (double)sample->accel_g[1],
                    (double)sample->accel_g[2], (double)sample->gyro_dps[0],
                    (double)sample->gyro_dps[1], (double)sample->gyro_dps[2],
                    (double)sample->imu_temperature_c)) { goto failed; }
    } else if (!append(&writer, "null")) { goto failed; }
    if ((sample->valid_mask & HUB_BMP280_VALID) != 0U) {
        if (!append(&writer,
                    ",\"baro\":{\"temperature_c\":%.2f,\"pressure_pa\":%.1f,\"altitude_m\":%.2f}",
                    (double)sample->baro_temperature_c, (double)sample->pressure_pa,
                    (double)sample->altitude_m)) { goto failed; }
    } else if (!append(&writer, ",\"baro\":null")) { goto failed; }
    if (!append(&writer,
                ",\"health\":{\"i2c_retries\":%lu,\"i2c_errors\":%lu,\"queue_drops\":%lu,"
                "\"deadline_misses\":%lu,\"watchdog\":\"%s\"",
                (unsigned long)counters->i2c_retries, (unsigned long)counters->i2c_errors,
                (unsigned long)(counters->raw_queue_drops + counters->telemetry_queue_drops),
                (unsigned long)counters->deadline_misses,
                status->watchdog_fed ? "fed" : "withheld")) { goto failed; }
    if (identity != NULL && !append(&writer,
                ",\"watchdog_enabled\":%s,\"reset_flags\":%lu",
                identity->watchdog_enabled ? "true" : "false",
                (unsigned long)identity->reset_flags)) { goto failed; }
    if (!append(&writer,
                "},\"timing\":{\"acquisition_us\":%lu,\"processing_us\":%lu}}\n",
                (unsigned long)sample->acquisition_time_us,
                (unsigned long)sample->processing_time_us)) { goto failed; }
    return (int)writer.used;

failed:
    buffer[0] = '\0';
    return -1;
}

int hub_telemetry_format(char *buffer, size_t capacity, const HubProcessedSample *sample,
                         const HubStatus *status, const HubCounters *counters) {
    return format_frame(buffer, capacity, sample, status, counters, NULL);
}

int hub_telemetry_format_v3(char *buffer, size_t capacity, const HubProcessedSample *sample,
                          const HubStatus *status, const HubCounters *counters,
                          const HubTelemetryIdentity *identity) {
    if (identity == NULL) {
        if (buffer != NULL && capacity > 0U) { buffer[0] = '\0'; }
        return -1;
    }
    return format_frame(buffer, capacity, sample, status, counters, identity);
}
