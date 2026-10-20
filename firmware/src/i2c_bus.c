#include "sensor_hub/i2c_bus.h"

static bool acquire(HubI2cBus *bus, uint32_t timeout_ms) {
    if (bus->lock == NULL) {
        return true;
    }
    if (!bus->lock(bus->context, timeout_ms)) {
        bus->lock_timeouts++;
        bus->errors++;
        return false;
    }
    return true;
}

static void release(HubI2cBus *bus) {
    if (bus->unlock != NULL) {
        bus->unlock(bus->context);
    }
}

bool hub_i2c_read(HubI2cBus *bus, uint8_t address, uint8_t reg,
                  uint8_t *data, size_t length, uint32_t lock_timeout_ms) {
    unsigned int attempt;
    bool ok = false;

    if (bus == NULL || bus->read == NULL || data == NULL || length == 0U ||
        (bus->lock == NULL) != (bus->unlock == NULL)) {
        return false;
    }
    if (!acquire(bus, lock_timeout_ms)) {
        return false;
    }

    for (attempt = 0U; attempt <= bus->max_retries; ++attempt) {
        bus->transactions++;
        if (bus->read(bus->context, address, reg, data, length)) {
            ok = true;
            break;
        }
        if (attempt < bus->max_retries) {
            bus->retries++;
        }
    }
    if (!ok) {
        bus->errors++;
    }
    release(bus);
    return ok;
}

bool hub_i2c_write(HubI2cBus *bus, uint8_t address, uint8_t reg,
                   const uint8_t *data, size_t length, uint32_t lock_timeout_ms) {
    unsigned int attempt;
    bool ok = false;

    if (bus == NULL || bus->write == NULL || data == NULL || length == 0U ||
        (bus->lock == NULL) != (bus->unlock == NULL)) {
        return false;
    }
    if (!acquire(bus, lock_timeout_ms)) {
        return false;
    }

    for (attempt = 0U; attempt <= bus->max_retries; ++attempt) {
        bus->transactions++;
        if (bus->write(bus->context, address, reg, data, length)) {
            ok = true;
            break;
        }
        if (attempt < bus->max_retries) {
            bus->retries++;
        }
    }
    if (!ok) {
        bus->errors++;
    }
    release(bus);
    return ok;
}
