#ifndef SENSOR_HUB_BMP280_H
#define SENSOR_HUB_BMP280_H

#include <stdbool.h>
#include <stdint.h>

#include "sensor_hub/i2c_bus.h"

typedef struct {
    HubI2cBus *bus;
    uint8_t address;
    bool initialized;
    uint16_t dig_t1;
    int16_t dig_t2;
    int16_t dig_t3;
    uint16_t dig_p1;
    int16_t dig_p2;
    int16_t dig_p3;
    int16_t dig_p4;
    int16_t dig_p5;
    int16_t dig_p6;
    int16_t dig_p7;
    int16_t dig_p8;
    int16_t dig_p9;
    int32_t t_fine;
} HubBmp280;

typedef struct {
    int32_t temperature_centi_c;
    uint32_t pressure_pa;
} HubBmp280Reading;

bool hub_bmp280_init(HubBmp280 *sensor, HubI2cBus *bus, uint8_t address);
bool hub_bmp280_read(HubBmp280 *sensor, HubBmp280Reading *reading);
bool hub_bmp280_compensate(HubBmp280 *sensor, int32_t raw_temperature,
                           int32_t raw_pressure, HubBmp280Reading *reading);

#endif
