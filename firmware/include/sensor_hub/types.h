#ifndef SENSOR_HUB_TYPES_H
#define SENSOR_HUB_TYPES_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    HUB_TASK_ACQUISITION = 0,
    HUB_TASK_PROCESSING,
    HUB_TASK_TELEMETRY,
    HUB_TASK_HEALTH,
    HUB_TASK_COUNT
} HubTaskId;

typedef struct {
    uint32_t sequence;
    uint64_t timestamp_us;
    uint8_t valid_mask;
    int16_t accel_raw[3];
    int16_t gyro_raw[3];
    int16_t imu_temp_raw;
    int32_t baro_temperature_centi_c;
    uint32_t pressure_pa;
    uint32_t acquisition_time_us;
} HubRawSample;

typedef struct {
    uint32_t sequence;
    uint64_t timestamp_us;
    uint8_t valid_mask;
    float accel_g[3];
    float gyro_dps[3];
    float imu_temperature_c;
    float baro_temperature_c;
    float pressure_pa;
    float altitude_m;
    uint32_t acquisition_time_us;
    uint32_t processing_time_us;
} HubProcessedSample;

typedef struct {
    uint32_t samples_requested;
    uint32_t samples_acquired;
    uint32_t samples_processed;
    uint32_t telemetry_frames;
    uint32_t sample_tick_overruns;
    uint32_t raw_queue_drops;
    uint32_t telemetry_queue_drops;
    uint32_t sensor_disconnects;
    uint32_t sensor_reconnects;
    uint32_t i2c_transactions;
    uint32_t i2c_retries;
    uint32_t i2c_errors;
    uint32_t deadline_misses;
    uint32_t watchdog_feeds;
    uint32_t watchdog_withheld;
    uint32_t max_acquisition_us;
    uint32_t max_processing_us;
} HubCounters;

typedef struct {
    bool mpu6050_online;
    bool bmp280_online;
    bool watchdog_fed;
    uint8_t mpu6050_consecutive_errors;
    uint8_t bmp280_consecutive_errors;
} HubStatus;

#endif
