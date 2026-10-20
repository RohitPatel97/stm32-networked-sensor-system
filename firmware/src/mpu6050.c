#include "sensor_hub/mpu6050.h"

#define MPU6050_REG_SMPLRT_DIV 0x19U
#define MPU6050_REG_CONFIG     0x1AU
#define MPU6050_REG_GYRO_CFG   0x1BU
#define MPU6050_REG_ACCEL_CFG  0x1CU
#define MPU6050_REG_DATA       0x3BU
#define MPU6050_REG_PWR_MGMT_1 0x6BU
#define MPU6050_REG_WHO_AM_I   0x75U

static int16_t be_i16(const uint8_t *bytes) {
    return (int16_t)(((uint16_t)bytes[0] << 8U) | bytes[1]);
}

bool hub_mpu6050_init(HubMpu6050 *sensor, HubI2cBus *bus, uint8_t address) {
    uint8_t identity = 0U;
    uint8_t value;

    if (sensor == NULL || bus == NULL) {
        return false;
    }
    sensor->bus = bus;
    sensor->address = address;
    sensor->initialized = false;
    if (!hub_i2c_read(bus, address, MPU6050_REG_WHO_AM_I, &identity, 1U, 5U) ||
        identity != 0x68U) {
        return false;
    }

    value = 0x01U; /* PLL with X-axis gyro reference; wake from sleep. */
    if (!hub_i2c_write(bus, address, MPU6050_REG_PWR_MGMT_1, &value, 1U, 5U)) {
        return false;
    }
    value = 0x04U; /* 1 kHz / (1 + 4) = 200 Hz internal sample rate. */
    if (!hub_i2c_write(bus, address, MPU6050_REG_SMPLRT_DIV, &value, 1U, 5U)) {
        return false;
    }
    value = 0x03U; /* ~44 Hz gyro / ~42 Hz accel DLPF. */
    if (!hub_i2c_write(bus, address, MPU6050_REG_CONFIG, &value, 1U, 5U)) {
        return false;
    }
    value = 0x00U; /* +/-250 dps. */
    if (!hub_i2c_write(bus, address, MPU6050_REG_GYRO_CFG, &value, 1U, 5U)) {
        return false;
    }
    value = 0x00U; /* +/-2 g. */
    sensor->initialized = hub_i2c_write(bus, address, MPU6050_REG_ACCEL_CFG,
                                       &value, 1U, 5U);
    return sensor->initialized;
}

bool hub_mpu6050_read(HubMpu6050 *sensor, HubMpu6050Reading *reading) {
    uint8_t raw[14];

    if (sensor == NULL || sensor->bus == NULL || reading == NULL) {
        return false;
    }
    if (!sensor->initialized &&
        !hub_mpu6050_init(sensor, sensor->bus, sensor->address)) {
        return false;
    }
    if (!hub_i2c_read(sensor->bus, sensor->address, MPU6050_REG_DATA,
                      raw, sizeof(raw), 5U)) {
        sensor->initialized = false;
        return false;
    }
    reading->accel[0] = be_i16(&raw[0]);
    reading->accel[1] = be_i16(&raw[2]);
    reading->accel[2] = be_i16(&raw[4]);
    reading->temperature = be_i16(&raw[6]);
    reading->gyro[0] = be_i16(&raw[8]);
    reading->gyro[1] = be_i16(&raw[10]);
    reading->gyro[2] = be_i16(&raw[12]);
    return true;
}
