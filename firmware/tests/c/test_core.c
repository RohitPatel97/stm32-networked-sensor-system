#include <stdbool.h>
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sensor_hub/bmp280.h"
#include "sensor_hub/config.h"
#include "sensor_hub/health.h"
#include "sensor_hub/i2c_bus.h"
#include "sensor_hub/processing.h"
#include "sensor_hub/sensor_state.h"
#include "sensor_hub/telemetry.h"

#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                      \
            fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, \
                    #condition);                                                \
            exit(EXIT_FAILURE);                                                 \
        }                                                                       \
    } while (0)

typedef struct {
    unsigned int calls;
    unsigned int fail_count;
    unsigned int locks;
    unsigned int unlocks;
} FakeBus;

static bool fake_lock(void *context, uint32_t timeout_ms) {
    FakeBus *fake = (FakeBus *)context;
    (void)timeout_ms;
    fake->locks++;
    return true;
}

static void fake_unlock(void *context) {
    ((FakeBus *)context)->unlocks++;
}

static bool fake_read(void *context, uint8_t address, uint8_t reg,
                      uint8_t *data, size_t length) {
    FakeBus *fake = (FakeBus *)context;
    (void)address;
    (void)reg;
    fake->calls++;
    if (fake->calls <= fake->fail_count) {
        return false;
    }
    memset(data, 0xA5, length);
    return true;
}

static bool fake_write(void *context, uint8_t address, uint8_t reg,
                       const uint8_t *data, size_t length) {
    uint8_t ignored[1];
    (void)data;
    (void)length;
    return fake_read(context, address, reg, ignored, sizeof(ignored));
}

static void test_i2c_retry_and_mutex(void) {
    FakeBus fake = {0U, 1U, 0U, 0U};
    HubI2cBus bus = {&fake, fake_lock, fake_unlock, fake_read, fake_write,
                     2U, 0U, 0U, 0U, 0U};
    uint8_t value = 0U;
    CHECK(hub_i2c_read(&bus, 0x68U, 0x75U, &value, 1U, 5U));
    CHECK(value == 0xA5U);
    CHECK(bus.transactions == 2U);
    CHECK(bus.retries == 1U);
    CHECK(bus.errors == 0U);
    CHECK(fake.locks == 1U && fake.unlocks == 1U);

    fake.calls = 0U;
    fake.fail_count = 99U;
    CHECK(!hub_i2c_read(&bus, 0x68U, 0x75U, &value, 1U, 5U));
    CHECK(bus.transactions == 5U);
    CHECK(bus.retries == 3U);
    CHECK(bus.errors == 1U);
    CHECK(fake.locks == 2U && fake.unlocks == 2U);
}

static void test_bmp280_datasheet_vector(void) {
    HubBmp280 bmp = {0};
    HubBmp280Reading reading;
    const int32_t invalid_raw[] = {-1, 0x80000, 0x100000};
    size_t index;
    bmp.dig_t1 = 27504U;
    bmp.dig_t2 = 26435;
    bmp.dig_t3 = -1000;
    bmp.dig_p1 = 36477U;
    bmp.dig_p2 = -10685;
    bmp.dig_p3 = 3024;
    bmp.dig_p4 = 2855;
    bmp.dig_p5 = 140;
    bmp.dig_p6 = -7;
    bmp.dig_p7 = 15500;
    bmp.dig_p8 = -14600;
    bmp.dig_p9 = 6000;
    CHECK(hub_bmp280_compensate(&bmp, 519888, 415148, &reading));
    CHECK(reading.temperature_centi_c == 2508);
    CHECK(reading.pressure_pa >= 100650U && reading.pressure_pa <= 100656U);

    /* Signed calibration coefficients exercise the negative intermediate path
       that previously used undefined signed left shifts. Invalid conversions
       must leave caller-owned output intact instead of publishing stale data. */
    reading.temperature_centi_c = 12345;
    reading.pressure_pa = 67890U;
    for (index = 0U; index < sizeof(invalid_raw) / sizeof(invalid_raw[0]); ++index) {
        CHECK(!hub_bmp280_compensate(&bmp, invalid_raw[index], 415148, &reading));
        CHECK(!hub_bmp280_compensate(&bmp, 519888, invalid_raw[index], &reading));
        CHECK(reading.temperature_centi_c == 12345 && reading.pressure_pa == 67890U);
    }
    bmp.dig_p1 = 0U;
    CHECK(!hub_bmp280_compensate(&bmp, 519888, 415148, &reading));

    /* Corrupt trim would overflow the unguarded pressure multiplier. */
    bmp.dig_t1 = 0U;
    bmp.dig_t2 = INT16_MAX;
    bmp.dig_t3 = INT16_MAX;
    bmp.dig_p1 = UINT16_MAX;
    bmp.dig_p2 = INT16_MAX;
    bmp.dig_p3 = INT16_MAX;
    CHECK(!hub_bmp280_compensate(&bmp, 0xFFFFF, 415148, &reading));
    CHECK(reading.temperature_centi_c == 12345 && reading.pressure_pa == 67890U);
}

static void test_sensor_state_transitions(void) {
    HubCounters counters = {0};
    bool online = true;
    uint8_t failures = 0U;
    unsigned int attempt;

    /* Intermittent failures must not accumulate across a successful read. */
    hub_sensor_state_update(false, &online, &failures, &counters);
    hub_sensor_state_update(false, &online, &failures, &counters);
    CHECK(online && failures == 2U && counters.sensor_disconnects == 0U);
    hub_sensor_state_update(true, &online, &failures, &counters);
    CHECK(online && failures == 0U && counters.sensor_reconnects == 0U);

    for (attempt = 0U; attempt < HUB_SENSOR_DISCONNECT_THRESHOLD; ++attempt) {
        hub_sensor_state_update(false, &online, &failures, &counters);
    }
    CHECK(!online && counters.sensor_disconnects == 1U);
    /* Saturating the failure count prevents long outages from wrapping to 0. */
    for (attempt = 0U; attempt < 300U; ++attempt) {
        hub_sensor_state_update(false, &online, &failures, &counters);
    }
    CHECK(!online && failures == UINT8_MAX && counters.sensor_disconnects == 1U);
    hub_sensor_state_update(true, &online, &failures, &counters);
    CHECK(online && failures == 0U && counters.sensor_reconnects == 1U);
    hub_sensor_state_update(true, &online, &failures, &counters);
    CHECK(counters.sensor_reconnects == 1U);
}

static void test_health_deadline_boundaries_and_wrap(void) {
    HubHealthMonitor health;
    uint32_t misses = 0U;
    unsigned int task;
    hub_health_init(&health);
    CHECK(!hub_health_all_tasks_alive(&health, 0U, &misses));
    CHECK(misses == HUB_TASK_COUNT);
    for (task = 0U; task < HUB_TASK_COUNT; ++task) {
        hub_health_heartbeat(&health, (HubTaskId)task, UINT32_MAX - 15U);
    }
    /* At now=14, elapsed time across wrap is 30 ms: exactly the ACQ deadline. */
    CHECK(hub_health_all_tasks_alive(&health, 14U, &misses));
    CHECK(misses == 0U);
    CHECK(!hub_health_all_tasks_alive(&health, 15U, &misses));
    CHECK(misses == 1U);
}

static void test_processing_health_and_telemetry(void) {
    HubRawSample raw = {0};
    HubProcessedSample processed;
    HubHealthMonitor health;
    HubStatus status = {true, true, true, 0U, 0U};
    HubCounters counters = {0};
    char frame[HUB_TELEMETRY_BUFFER_SIZE];
    uint32_t misses = 0U;
    unsigned int task;

    raw.sequence = 42U;
    raw.timestamp_us = 420000U;
    raw.valid_mask = HUB_MPU6050_VALID | HUB_BMP280_VALID;
    raw.accel_raw[0] = 16384;
    raw.accel_raw[2] = 16384;
    raw.gyro_raw[1] = 131;
    raw.imu_temp_raw = 0;
    raw.baro_temperature_centi_c = 2508;
    raw.pressure_pa = 100653U;
    raw.acquisition_time_us = 91U;
    hub_process_sample(&raw, &processed);
    processed.processing_time_us = 33U;
    CHECK(processed.accel_g[0] > 0.999F && processed.accel_g[0] < 1.001F);
    CHECK(processed.gyro_dps[1] > 0.999F && processed.gyro_dps[1] < 1.001F);
    CHECK(processed.altitude_m > 50.0F && processed.altitude_m < 60.0F);

    hub_health_init(&health);
    for (task = 0U; task < HUB_TASK_COUNT; ++task) {
        hub_health_heartbeat(&health, (HubTaskId)task, 100U);
    }
    CHECK(hub_health_all_tasks_alive(&health, 110U, &misses));
    CHECK(misses == 0U);
    CHECK(!hub_health_all_tasks_alive(&health, 2000U, &misses));
    CHECK(misses == HUB_TASK_COUNT);

    counters.i2c_retries = 2U;
    CHECK(hub_telemetry_format(frame, sizeof(frame), &processed,
                               &status, &counters) > 0);
    CHECK(strstr(frame, "\"schema\":1") != NULL);
    CHECK(strstr(frame, "\"seq\":42") != NULL);
    CHECK(strstr(frame, "\"i2c_retries\":2") != NULL);
    CHECK(frame[strlen(frame) - 1U] == '\n');

    processed.valid_mask = HUB_MPU6050_VALID;
    status.bmp280_online = false;
    CHECK(hub_telemetry_format(frame, sizeof(frame), &processed,
                               &status, &counters) > 0);
    CHECK(strstr(frame, "\"bmp280\":\"offline\"") != NULL);
    CHECK(strstr(frame, "\"baro\":null") != NULL);
    CHECK(strstr(frame, "\"accel_g\":[") != NULL);
}

static void test_telemetry_failure_contract(void) {
    HubProcessedSample sample = {0};
    HubStatus status = {0};
    HubCounters counters = {0};
    char storage[HUB_TELEMETRY_BUFFER_SIZE + 2U];
    char *frame = storage + 1;
    char expected[HUB_TELEMETRY_BUFFER_SIZE];
    float *fields[] = {&sample.accel_g[0], &sample.accel_g[1], &sample.accel_g[2],
                      &sample.gyro_dps[0], &sample.gyro_dps[1], &sample.gyro_dps[2],
                      &sample.imu_temperature_c, &sample.baro_temperature_c,
                      &sample.pressure_pa, &sample.altitude_m};
    const float invalid[] = {NAN, INFINITY, -INFINITY};
    size_t field;
    size_t value;
    int length;
    sample.valid_mask = HUB_MPU6050_VALID | HUB_BMP280_VALID;
    memset(storage, '#', sizeof(storage));
    length = hub_telemetry_format(expected, sizeof(expected), &sample, &status, &counters);
    CHECK(length > 0);
    CHECK(hub_telemetry_format(frame, (size_t)length + 1U, &sample, &status, &counters) == length);
    CHECK(strcmp(frame, expected) == 0);
    CHECK(storage[0] == '#' && frame[length + 1] == '#');
    CHECK(hub_telemetry_format(frame, (size_t)length, &sample, &status, &counters) == -1);
    CHECK(frame[0] == '\0');
    CHECK(hub_telemetry_format(frame, 1U, &sample, &status, &counters) == -1);
    CHECK(frame[0] == '\0');
    for (field = 0U; field < sizeof(fields) / sizeof(fields[0]); ++field) {
        for (value = 0U; value < sizeof(invalid) / sizeof(invalid[0]); ++value) {
            *fields[field] = invalid[value];
            strcpy(frame, "stale telemetry");
            CHECK(hub_telemetry_format(frame, HUB_TELEMETRY_BUFFER_SIZE, &sample, &status, &counters) == -1);
            CHECK(frame[0] == '\0');
        }
        *fields[field] = 0.0F;
    }
    for (field = 0U; field < sizeof(fields) / sizeof(fields[0]); ++field) { *fields[field] = FLT_MAX; }
    strcpy(frame, "stale telemetry");
    CHECK(hub_telemetry_format(frame, 32U, &sample, &status, &counters) == -1);
    CHECK(frame[0] == '\0');
    /* Invalid sensor fields are deliberately ignored and encoded as null. */
    sample.valid_mask = 0U;
    sample.accel_g[0] = NAN;
    sample.pressure_pa = INFINITY;
    CHECK(hub_telemetry_format(frame, HUB_TELEMETRY_BUFFER_SIZE, &sample, &status, &counters) > 0);
    CHECK(strstr(frame, "\"imu\":null,\"baro\":null") != NULL);
    CHECK(hub_telemetry_format(frame, HUB_TELEMETRY_BUFFER_SIZE, NULL, &status, &counters) == -1);
    CHECK(frame[0] == '\0');
    strcpy(frame, "stale telemetry");
    CHECK(hub_telemetry_format(frame, HUB_TELEMETRY_BUFFER_SIZE, &sample, NULL, &counters) == -1);
    CHECK(frame[0] == '\0');
    strcpy(frame, "stale telemetry");
    CHECK(hub_telemetry_format(frame, HUB_TELEMETRY_BUFFER_SIZE, &sample, &status, NULL) == -1);
    CHECK(frame[0] == '\0');
    frame[0] = '#';
    CHECK(hub_telemetry_format(frame, 0U, &sample, &status, &counters) == -1);
    CHECK(frame[0] == '#');
    CHECK(hub_telemetry_format(NULL, 10U, &sample, &status, &counters) == -1);
}

int main(void) {
    test_i2c_retry_and_mutex();
    test_bmp280_datasheet_vector();
    test_sensor_state_transitions();
    test_health_deadline_boundaries_and_wrap();
    test_processing_health_and_telemetry();
    test_telemetry_failure_contract();
    puts("6 core test groups passed");
    return 0;
}
