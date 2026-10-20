#ifndef SENSOR_HUB_I2C_BUS_H
#define SENSOR_HUB_I2C_BUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef bool (*HubI2cLockFn)(void *context, uint32_t timeout_ms);
typedef void (*HubI2cUnlockFn)(void *context);
typedef bool (*HubI2cReadFn)(void *context, uint8_t address, uint8_t reg,
                             uint8_t *data, size_t length);
typedef bool (*HubI2cWriteFn)(void *context, uint8_t address, uint8_t reg,
                              const uint8_t *data, size_t length);

typedef struct {
    void *context;
    HubI2cLockFn lock;
    HubI2cUnlockFn unlock;
    HubI2cReadFn read;
    HubI2cWriteFn write;
    uint8_t max_retries;
    uint32_t transactions;
    uint32_t retries;
    uint32_t errors;
    uint32_t lock_timeouts;
} HubI2cBus;

bool hub_i2c_read(HubI2cBus *bus, uint8_t address, uint8_t reg,
                  uint8_t *data, size_t length, uint32_t lock_timeout_ms);
bool hub_i2c_write(HubI2cBus *bus, uint8_t address, uint8_t reg,
                   const uint8_t *data, size_t length, uint32_t lock_timeout_ms);

#endif
