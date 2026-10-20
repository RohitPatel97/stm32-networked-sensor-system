#include "hub_stm32_port.h"

#include "sensor_hub/config.h"

static bool port_lock(void *context, uint32_t timeout_ms) {
    HubStm32Port *port = (HubStm32Port *)context;
    return xSemaphoreTake(port->i2c_mutex, pdMS_TO_TICKS(timeout_ms)) == pdTRUE;
}

static void port_unlock(void *context) {
    HubStm32Port *port = (HubStm32Port *)context;
    (void)xSemaphoreGive(port->i2c_mutex);
}

static bool port_read(void *context, uint8_t address, uint8_t reg,
                      uint8_t *data, size_t length) {
    HubStm32Port *port = (HubStm32Port *)context;
    if (length > UINT16_MAX) {
        return false;
    }
    return HAL_I2C_Mem_Read(port->i2c, (uint16_t)address << 1U, reg,
                            I2C_MEMADD_SIZE_8BIT, data, (uint16_t)length,
                            5U) == HAL_OK;
}

static bool port_write(void *context, uint8_t address, uint8_t reg,
                       const uint8_t *data, size_t length) {
    HubStm32Port *port = (HubStm32Port *)context;
    if (length > UINT16_MAX) {
        return false;
    }
    return HAL_I2C_Mem_Write(port->i2c, (uint16_t)address << 1U, reg,
                             I2C_MEMADD_SIZE_8BIT, (uint8_t *)data,
                             (uint16_t)length, 5U) == HAL_OK;
}

void hub_stm32_port_init(HubStm32Port *port, I2C_HandleTypeDef *i2c,
                         UART_HandleTypeDef *uart,
                         IWDG_HandleTypeDef *watchdog,
                         SemaphoreHandle_t i2c_mutex) {
    configASSERT(port != NULL);
    configASSERT(i2c != NULL);
    configASSERT(uart != NULL);
    configASSERT(i2c_mutex != NULL);
    port->i2c = i2c;
    port->uart = uart;
    port->watchdog = watchdog;
    port->i2c_mutex = i2c_mutex;
}

void hub_stm32_bus_init(HubI2cBus *bus, HubStm32Port *port) {
    configASSERT(bus != NULL);
    configASSERT(port != NULL);
    bus->context = port;
    bus->lock = port_lock;
    bus->unlock = port_unlock;
    bus->read = port_read;
    bus->write = port_write;
    bus->max_retries = HUB_I2C_MAX_RETRIES;
    bus->transactions = 0U;
    bus->retries = 0U;
    bus->errors = 0U;
    bus->lock_timeouts = 0U;
}

bool hub_stm32_uart_write(HubStm32Port *port, const uint8_t *data,
                          size_t length, uint32_t timeout_ms) {
    if (port == NULL || data == NULL || length > UINT16_MAX) {
        return false;
    }
    return HAL_UART_Transmit(port->uart, (uint8_t *)data, (uint16_t)length,
                             timeout_ms) == HAL_OK;
}

void hub_stm32_watchdog_feed(HubStm32Port *port) {
    if (port != NULL && port->watchdog != NULL &&
        port->watchdog->Instance == IWDG) {
        (void)HAL_IWDG_Refresh(port->watchdog);
    }
}

void hub_stm32_dwt_init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

uint32_t hub_stm32_dwt_cycles(void) {
    return DWT->CYCCNT;
}

uint32_t hub_stm32_cycles_to_us(uint32_t cycles) {
    const uint32_t cycles_per_us = SystemCoreClock / 1000000U;
    return cycles_per_us == 0U ? 0U : cycles / cycles_per_us;
}
