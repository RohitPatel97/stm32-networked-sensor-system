#include "sensor_hub/processing.h"
#include "sensor_hub/config.h"

#include <math.h>
#include <string.h>

void hub_process_sample(const HubRawSample *raw, HubProcessedSample *processed) {
    unsigned int axis;

    if (raw == NULL || processed == NULL) {
        return;
    }
    memset(processed, 0, sizeof(*processed));
    processed->sequence = raw->sequence;
    processed->timestamp_us = raw->timestamp_us;
    processed->valid_mask = raw->valid_mask;
    processed->acquisition_time_us = raw->acquisition_time_us;

    if ((raw->valid_mask & HUB_MPU6050_VALID) != 0U) {
        for (axis = 0U; axis < 3U; ++axis) {
            processed->accel_g[axis] = (float)raw->accel_raw[axis] / 16384.0F;
            processed->gyro_dps[axis] = (float)raw->gyro_raw[axis] / 131.0F;
        }
        processed->imu_temperature_c =
            ((float)raw->imu_temp_raw / 340.0F) + 36.53F;
    }
    if ((raw->valid_mask & HUB_BMP280_VALID) != 0U) {
        float ratio;
        processed->baro_temperature_c =
            (float)raw->baro_temperature_centi_c / 100.0F;
        processed->pressure_pa = (float)raw->pressure_pa;
        ratio = processed->pressure_pa / 101325.0F;
        processed->altitude_m = 44330.0F * (1.0F - powf(ratio, 0.19029495F));
    }
}
