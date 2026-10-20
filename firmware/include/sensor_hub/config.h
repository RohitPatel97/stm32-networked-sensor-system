#ifndef SENSOR_HUB_CONFIG_H
#define SENSOR_HUB_CONFIG_H

#include <stdint.h>

#define HUB_SAMPLE_RATE_HZ              100U
#define HUB_TELEMETRY_RATE_HZ           10U
#define HUB_SENSOR_DISCONNECT_THRESHOLD 3U
#define HUB_I2C_MAX_RETRIES              2U
#define HUB_TELEMETRY_BUFFER_SIZE        1024U

#define HUB_MPU6050_ADDRESS              0x68U
#define HUB_BMP280_ADDRESS               0x76U

#define HUB_MPU6050_VALID                (1U << 0)
#define HUB_BMP280_VALID                 (1U << 1)

#endif
