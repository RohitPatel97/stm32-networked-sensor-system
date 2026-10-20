#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sensor_hub/bmp280.h"
#include "sensor_hub/config.h"
#include "sensor_hub/health.h"
#include "sensor_hub/i2c_bus.h"
#include "sensor_hub/mpu6050.h"
#include "sensor_hub/processing.h"
#include "sensor_hub/sensor_state.h"
#include "sensor_hub/telemetry.h"
#include "sensor_hub/types.h"

#define RAW_QUEUE_STORAGE 16U
#define MAX_DURATION_MS 3600000U

typedef enum {
    DISCONNECT_NONE,
    DISCONNECT_MPU,
    DISCONNECT_BMP,
    DISCONNECT_ALL
} DisconnectTarget;

typedef struct {
    uint32_t now_ms;
    uint32_t sample_index;
    uint32_t io_calls;
    uint32_t nack_every;
    uint32_t disconnect_at_ms;
    uint32_t reconnect_at_ms;
    DisconnectTarget disconnect;
    bool locked;
} SimI2c;

typedef struct {
    HubRawSample values[RAW_QUEUE_STORAGE];
    size_t head;
    size_t tail;
    size_t count;
    size_t capacity;
} RawQueue;

typedef struct {
    uint32_t duration_ms;
    uint32_t processor_period_ms;
    size_t queue_capacity;
    uint32_t nack_every;
    uint32_t disconnect_at_ms;
    uint32_t reconnect_at_ms;
    DisconnectTarget disconnect;
    HubTaskId stalled_task;
    bool stall_enabled;
    uint32_t stall_at_ms;
    uint64_t boot_id;
    const char *device_id;
} Options;

static bool sim_lock(void *context, uint32_t timeout_ms) {
    SimI2c *sim = (SimI2c *)context;
    (void)timeout_ms;
    if (sim->locked) {
        return false;
    }
    sim->locked = true;
    return true;
}

static void sim_unlock(void *context) {
    ((SimI2c *)context)->locked = false;
}

static bool target_is_disconnected(const SimI2c *sim, uint8_t address) {
    if (sim->now_ms < sim->disconnect_at_ms ||
        sim->now_ms >= sim->reconnect_at_ms) {
        return false;
    }
    return sim->disconnect == DISCONNECT_ALL ||
           (sim->disconnect == DISCONNECT_MPU && address == HUB_MPU6050_ADDRESS) ||
           (sim->disconnect == DISCONNECT_BMP && address == HUB_BMP280_ADDRESS);
}

static bool sim_io_allowed(SimI2c *sim, uint8_t address) {
    sim->io_calls++;
    if (target_is_disconnected(sim, address)) {
        return false;
    }
    return sim->nack_every == 0U || (sim->io_calls % sim->nack_every) != 0U;
}

static void put_le16(uint8_t *bytes, uint16_t value) {
    bytes[0] = (uint8_t)(value & 0xFFU);
    bytes[1] = (uint8_t)(value >> 8U);
}

static void put_be16(uint8_t *bytes, int16_t value) {
    const uint16_t raw = (uint16_t)value;
    bytes[0] = (uint8_t)(raw >> 8U);
    bytes[1] = (uint8_t)(raw & 0xFFU);
}

static bool sim_read(void *context, uint8_t address, uint8_t reg,
                     uint8_t *data, size_t length) {
    SimI2c *sim = (SimI2c *)context;
    if (!sim_io_allowed(sim, address)) {
        return false;
    }
    memset(data, 0, length);

    if (address == HUB_MPU6050_ADDRESS && reg == 0x75U && length == 1U) {
        data[0] = 0x68U;
        return true;
    }
    if (address == HUB_MPU6050_ADDRESS && reg == 0x3BU && length == 14U) {
        const int32_t phase = (int32_t)(sim->sample_index % 200U) - 100;
        put_be16(&data[0], (int16_t)(phase * 25));
        put_be16(&data[2], (int16_t)(-phase * 18));
        put_be16(&data[4], (int16_t)(16384 + phase * 2));
        put_be16(&data[6], (int16_t)(-3920 + phase));
        put_be16(&data[8], (int16_t)(phase * 3));
        put_be16(&data[10], (int16_t)(phase * -2));
        put_be16(&data[12], (int16_t)(phase));
        return true;
    }
    if (address == HUB_BMP280_ADDRESS && reg == 0xD0U && length == 1U) {
        data[0] = 0x58U;
        return true;
    }
    if (address == HUB_BMP280_ADDRESS && reg == 0x88U && length == 24U) {
        const int32_t calibration[12] = {
            27504, 26435, -1000, 36477, -10685, 3024,
            2855, 140, -7, 15500, -14600, 6000
        };
        size_t index;
        for (index = 0U; index < 12U; ++index) {
            put_le16(&data[index * 2U], (uint16_t)calibration[index]);
        }
        return true;
    }
    if (address == HUB_BMP280_ADDRESS && reg == 0xF7U && length == 6U) {
        const int32_t raw_p = 415148 + (int32_t)(sim->sample_index % 31U) - 15;
        const int32_t raw_t = 519888 + (int32_t)(sim->sample_index % 19U) - 9;
        data[0] = (uint8_t)((uint32_t)raw_p >> 12U);
        data[1] = (uint8_t)((uint32_t)raw_p >> 4U);
        data[2] = (uint8_t)((uint32_t)raw_p << 4U);
        data[3] = (uint8_t)((uint32_t)raw_t >> 12U);
        data[4] = (uint8_t)((uint32_t)raw_t >> 4U);
        data[5] = (uint8_t)((uint32_t)raw_t << 4U);
        return true;
    }
    return false;
}

static bool sim_write(void *context, uint8_t address, uint8_t reg,
                      const uint8_t *data, size_t length) {
    SimI2c *sim = (SimI2c *)context;
    (void)reg;
    (void)data;
    return length > 0U && sim_io_allowed(sim, address);
}

static bool raw_push(RawQueue *queue, const HubRawSample *sample) {
    if (queue->count >= queue->capacity) {
        return false;
    }
    queue->values[queue->tail] = *sample;
    queue->tail = (queue->tail + 1U) % RAW_QUEUE_STORAGE;
    queue->count++;
    return true;
}

static bool raw_pop(RawQueue *queue, HubRawSample *sample) {
    if (queue->count == 0U) {
        return false;
    }
    *sample = queue->values[queue->head];
    queue->head = (queue->head + 1U) % RAW_QUEUE_STORAGE;
    queue->count--;
    return true;
}

static bool task_stalled(const Options *options, HubTaskId task, uint32_t now_ms) {
    return options->stall_enabled && options->stalled_task == task &&
           now_ms >= options->stall_at_ms;
}

static void acquire_sample(HubMpu6050 *mpu, HubBmp280 *bmp, SimI2c *sim,
                           HubStatus *status, HubCounters *counters,
                           RawQueue *queue, uint32_t sequence) {
    HubRawSample raw;
    HubMpu6050Reading imu;
    HubBmp280Reading baro;
    const bool mpu_ok = hub_mpu6050_read(mpu, &imu);
    const bool bmp_ok = hub_bmp280_read(bmp, &baro);

    memset(&raw, 0, sizeof(raw));
    raw.sequence = sequence;
    raw.timestamp_us = (uint64_t)sim->now_ms * 1000ULL;
    raw.acquisition_time_us = 82U + (sequence % 11U) +
                              (!mpu_ok || !bmp_ok ? 40U : 0U);
    hub_sensor_state_update(mpu_ok, &status->mpu6050_online,
                        &status->mpu6050_consecutive_errors, counters);
    hub_sensor_state_update(bmp_ok, &status->bmp280_online,
                        &status->bmp280_consecutive_errors, counters);
    if (mpu_ok) {
        memcpy(raw.accel_raw, imu.accel, sizeof(raw.accel_raw));
        memcpy(raw.gyro_raw, imu.gyro, sizeof(raw.gyro_raw));
        raw.imu_temp_raw = imu.temperature;
        raw.valid_mask |= HUB_MPU6050_VALID;
    }
    if (bmp_ok) {
        raw.baro_temperature_centi_c = baro.temperature_centi_c;
        raw.pressure_pa = baro.pressure_pa;
        raw.valid_mask |= HUB_BMP280_VALID;
    }
    counters->samples_acquired++;
    if (raw.acquisition_time_us > counters->max_acquisition_us) {
        counters->max_acquisition_us = raw.acquisition_time_us;
    }
    if (!raw_push(queue, &raw)) {
        counters->raw_queue_drops++;
    }
}

static uint32_t parse_unsigned(const char *value, const char *name) {
    char *end = NULL;
    const char *digit = value;
    unsigned long parsed;
    /* strtoul accepts signs and whitespace, and unsigned long is 32-bit on
       Windows but commonly 64-bit on Linux. Reject both forms before casting. */
    while (*digit >= '0' && *digit <= '9') {
        ++digit;
    }
    errno = 0;
    parsed = strtoul(value, &end, 10);
    if (value[0] == '\0' || *digit != '\0' || end == value || *end != '\0' ||
        errno == ERANGE || parsed > UINT32_MAX) {
        fprintf(stderr, "invalid value for %s: %s\n", name, value);
        exit(2);
    }
    return (uint32_t)parsed;
}

static void usage(const char *program) {
    printf("Usage: %s [options]\n"
           "  --duration-ms N           1..3600000 ms (default 3000)\n"
           "  --processor-period-ms N   consumer period (default 10)\n"
           "  --queue-capacity N        1..16 (default 8)\n"
           "  --i2c-nack-every N        fail every Nth bus attempt; retry may recover\n"
           "  --disconnect TARGET       none|mpu|bmp|all\n"
           "  --disconnect-at-ms N      disconnect start\n"
           "  --reconnect-at-ms N       restore selected sensors after disconnect\n"
           "  --stall-task TASK         none|acquisition|processing|telemetry|health\n"
           "  --stall-at-ms N           task stall start\n"
           "  --boot-id N               known boot counter (default 1)\n"
           "  --device-id ID            stable device identity (default sim-hub)\n", program);
}

static uint64_t parse_boot_id(const char *value) {
    char *end;
    const char *digit = value;
    unsigned long long result;
    while (*digit >= '0' && *digit <= '9') { ++digit; }
    errno = 0;
    result = strtoull(value, &end, 10);
    if (value[0] == '\0' || *digit != '\0' || *end != '\0' || errno == ERANGE) {
        fprintf(stderr, "invalid boot-id\n"); exit(2);
    }
    return (uint64_t)result;
}

static Options parse_options(int argc, char **argv) {
    Options options = {3000U, 10U, 8U, 0U, UINT32_MAX, UINT32_MAX, DISCONNECT_NONE,
                       HUB_TASK_COUNT, false, UINT32_MAX, 1ULL, "sim-hub"};
    bool reconnect_requested = false;
    int index;
    for (index = 1; index < argc; ++index) {
        const char *argument = argv[index];
        if (strcmp(argument, "--help") == 0) {
            usage(argv[0]);
            exit(0);
        }
        if (index + 1 >= argc) {
            fprintf(stderr, "missing value after %s\n", argument);
            exit(2);
        }
        if (strcmp(argument, "--boot-id") == 0) {
            options.boot_id = parse_boot_id(argv[++index]);
        } else if (strcmp(argument, "--device-id") == 0) {
            size_t n;
            options.device_id = argv[++index];
            if (strlen(options.device_id) == 0U || strlen(options.device_id) > 64U) {
                fprintf(stderr, "invalid device-id length\n"); exit(2);
            }
            for (n = 0U; options.device_id[n] != '\0'; ++n) {
                const char c = options.device_id[n];
                if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                      (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) {
                    fprintf(stderr, "invalid device-id character\n"); exit(2);
                }
            }
        } else if (strcmp(argument, "--duration-ms") == 0) {
            options.duration_ms = (uint32_t)parse_unsigned(argv[++index], argument);
        } else if (strcmp(argument, "--processor-period-ms") == 0) {
            options.processor_period_ms = (uint32_t)parse_unsigned(argv[++index], argument);
        } else if (strcmp(argument, "--queue-capacity") == 0) {
            options.queue_capacity = (size_t)parse_unsigned(argv[++index], argument);
        } else if (strcmp(argument, "--i2c-nack-every") == 0) {
            options.nack_every = (uint32_t)parse_unsigned(argv[++index], argument);
        } else if (strcmp(argument, "--disconnect-at-ms") == 0) {
            options.disconnect_at_ms = (uint32_t)parse_unsigned(argv[++index], argument);
        } else if (strcmp(argument, "--reconnect-at-ms") == 0) {
            options.reconnect_at_ms = parse_unsigned(argv[++index], argument);
            reconnect_requested = true;
        } else if (strcmp(argument, "--stall-at-ms") == 0) {
            options.stall_at_ms = (uint32_t)parse_unsigned(argv[++index], argument);
        } else if (strcmp(argument, "--disconnect") == 0) {
            const char *value = argv[++index];
            if (strcmp(value, "none") == 0) options.disconnect = DISCONNECT_NONE;
            else if (strcmp(value, "mpu") == 0) options.disconnect = DISCONNECT_MPU;
            else if (strcmp(value, "bmp") == 0) options.disconnect = DISCONNECT_BMP;
            else if (strcmp(value, "all") == 0) options.disconnect = DISCONNECT_ALL;
            else { fprintf(stderr, "unknown disconnect target: %s\n", value); exit(2); }
        } else if (strcmp(argument, "--stall-task") == 0) {
            const char *value = argv[++index];
            options.stall_enabled = strcmp(value, "none") != 0;
            if (strcmp(value, "acquisition") == 0) options.stalled_task = HUB_TASK_ACQUISITION;
            else if (strcmp(value, "processing") == 0) options.stalled_task = HUB_TASK_PROCESSING;
            else if (strcmp(value, "telemetry") == 0) options.stalled_task = HUB_TASK_TELEMETRY;
            else if (strcmp(value, "health") == 0) options.stalled_task = HUB_TASK_HEALTH;
            else if (strcmp(value, "none") != 0) { fprintf(stderr, "unknown task: %s\n", value); exit(2); }
        } else {
            fprintf(stderr, "unknown option: %s\n", argument);
            exit(2);
        }
    }
    if (options.duration_ms == 0U || options.duration_ms > MAX_DURATION_MS) {
        fprintf(stderr, "duration must be 1..3600000 ms\n");
        exit(2);
    }
    if (reconnect_requested && (options.disconnect == DISCONNECT_NONE ||
        options.reconnect_at_ms <= options.disconnect_at_ms)) {
        fprintf(stderr, "reconnect requires a sensor target and a later time than disconnect\n");
        exit(2);
    }
    if (options.queue_capacity == 0U || options.queue_capacity > RAW_QUEUE_STORAGE ||
        options.processor_period_ms == 0U) {
        fprintf(stderr, "queue capacity must be 1..16 and periods must be non-zero\n");
        exit(2);
    }
    return options;
}

int main(int argc, char **argv) {
    const Options options = parse_options(argc, argv);
    SimI2c sim = {0U, 0U, 0U, options.nack_every, options.disconnect_at_ms,
                  options.reconnect_at_ms, options.disconnect, false};
    HubI2cBus bus = {&sim, sim_lock, sim_unlock, sim_read, sim_write,
                     HUB_I2C_MAX_RETRIES, 0U, 0U, 0U, 0U};
    HubMpu6050 mpu;
    HubBmp280 bmp;
    HubStatus status = {false, false, false, 0U, 0U};
    HubCounters counters = {0};
    HubHealthMonitor health;
    RawQueue queue = {0};
    HubProcessedSample latest = {0};
    bool have_latest = false;
    bool sample_pending = false;
    uint32_t sequence = 0U;
    uint32_t now_ms;

    queue.capacity = options.queue_capacity;
    hub_health_init(&health);
    status.mpu6050_online = hub_mpu6050_init(&mpu, &bus, HUB_MPU6050_ADDRESS);
    status.bmp280_online = hub_bmp280_init(&bmp, &bus, HUB_BMP280_ADDRESS);

    for (now_ms = 1U; now_ms <= options.duration_ms; ++now_ms) {
        sim.now_ms = now_ms;
        if ((now_ms % (1000U / HUB_SAMPLE_RATE_HZ)) == 0U) {
            counters.samples_requested++;
            if (sample_pending) {
                counters.sample_tick_overruns++;
            }
            sample_pending = true; /* Binary semaphore semantics. */
        }
        if (sample_pending && !task_stalled(&options, HUB_TASK_ACQUISITION, now_ms)) {
            sample_pending = false;
            sim.sample_index = sequence;
            acquire_sample(&mpu, &bmp, &sim, &status, &counters, &queue, sequence++);
            hub_health_heartbeat(&health, HUB_TASK_ACQUISITION, now_ms);
        }
        if ((now_ms % options.processor_period_ms) == 0U &&
            !task_stalled(&options, HUB_TASK_PROCESSING, now_ms)) {
            HubRawSample raw;
            if (raw_pop(&queue, &raw)) {
                hub_process_sample(&raw, &latest);
                latest.processing_time_us = 31U + (latest.sequence % 7U);
                if (latest.processing_time_us > counters.max_processing_us) {
                    counters.max_processing_us = latest.processing_time_us;
                }
                counters.samples_processed++;
                have_latest = true;
                /* Match the target: blocked input is not processing progress. */
                hub_health_heartbeat(&health, HUB_TASK_PROCESSING, now_ms);
            }
        }
        if ((now_ms % (1000U / HUB_TELEMETRY_RATE_HZ)) == 0U &&
            !task_stalled(&options, HUB_TASK_TELEMETRY, now_ms)) {
            char frame[HUB_TELEMETRY_BUFFER_SIZE];
            HubTelemetryIdentity identity = {options.device_id, options.boot_id,
                counters.telemetry_frames, now_ms, have_latest, false, 0U};
            counters.i2c_transactions = bus.transactions;
            counters.i2c_retries = bus.retries;
            counters.i2c_errors = bus.errors;
            if (hub_telemetry_format_v3(frame, sizeof(frame), &latest,
                                       &status, &counters, &identity) > 0) {
                fputs(frame, stdout);
                counters.telemetry_frames++;
            }
            hub_health_heartbeat(&health, HUB_TASK_TELEMETRY, now_ms);
        }
        if ((now_ms % 1000U) == 0U) {
            if (!task_stalled(&options, HUB_TASK_HEALTH, now_ms)) {
                uint32_t misses = 0U;
                hub_health_heartbeat(&health, HUB_TASK_HEALTH, now_ms);
                status.watchdog_fed = hub_health_all_tasks_alive(&health, now_ms, &misses);
                if (status.watchdog_fed) {
                    counters.watchdog_feeds++;
                } else {
                    counters.watchdog_withheld++;
                    counters.deadline_misses += misses;
                }
            } else {
                /* External IWDG observation: a stalled health task cannot feed. */
                status.watchdog_fed = false;
                counters.watchdog_withheld++;
                counters.deadline_misses++;
            }
        }
    }

    counters.i2c_transactions = bus.transactions;
    counters.i2c_retries = bus.retries;
    counters.i2c_errors = bus.errors;
    fprintf(stderr,
            "{\"type\":\"summary\",\"samples_requested\":%lu,"
            "\"samples_acquired\":%lu,\"samples_processed\":%lu,"
            "\"telemetry_frames\":%lu,\"i2c_transactions\":%lu,"
            "\"i2c_retries\":%lu,\"i2c_errors\":%lu,"
            "\"queue_drops\":%lu,\"tick_overruns\":%lu,"
            "\"sensor_disconnects\":%lu,\"sensor_reconnects\":%lu,\"watchdog_feeds\":%lu,"
            "\"watchdog_withheld\":%lu,\"deadline_misses\":%lu}\n",
            (unsigned long)counters.samples_requested,
            (unsigned long)counters.samples_acquired,
            (unsigned long)counters.samples_processed,
            (unsigned long)counters.telemetry_frames,
            (unsigned long)counters.i2c_transactions,
            (unsigned long)counters.i2c_retries,
            (unsigned long)counters.i2c_errors,
            (unsigned long)(counters.raw_queue_drops + counters.telemetry_queue_drops),
            (unsigned long)counters.sample_tick_overruns,
            (unsigned long)counters.sensor_disconnects,
            (unsigned long)counters.sensor_reconnects,
            (unsigned long)counters.watchdog_feeds,
            (unsigned long)counters.watchdog_withheld,
            (unsigned long)counters.deadline_misses);
    return 0;
}
