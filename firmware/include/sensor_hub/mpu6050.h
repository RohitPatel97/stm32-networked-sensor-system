#ifndef SENSOR_HUB_MPU6050_H
#define SENSOR_HUB_MPU6050_H

#include <stdbool.h>
#include <stdint.h>

#include "sensor_hub/i2c_bus.h"

typedef struct {
    HubI2cBus *bus;
    uint8_t address;
    bool initialized;
} HubMpu6050;

typedef struct {
    int16_t accel[3];
    int16_t temperature;
    int16_t gyro[3];
} HubMpu6050Reading;

bool hub_mpu6050_init(HubMpu6050 *sensor, HubI2cBus *bus, uint8_t address);
bool hub_mpu6050_read(HubMpu6050 *sensor, HubMpu6050Reading *reading);

#endif
