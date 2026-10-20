#ifndef HUB_STM32_PORT_H
#define HUB_STM32_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "FreeRTOS.h"
#include "semphr.h"
#include "stm32f4xx_hal.h"

#include "sensor_hub/i2c_bus.h"

typedef struct {
    I2C_HandleTypeDef *i2c;
    UART_HandleTypeDef *uart;
    IWDG_HandleTypeDef *watchdog;
    SemaphoreHandle_t i2c_mutex;
} HubStm32Port;

void hub_stm32_port_init(HubStm32Port *port, I2C_HandleTypeDef *i2c,
                         UART_HandleTypeDef *uart,
                         IWDG_HandleTypeDef *watchdog,
                         SemaphoreHandle_t i2c_mutex);
void hub_stm32_bus_init(HubI2cBus *bus, HubStm32Port *port);
bool hub_stm32_uart_write(HubStm32Port *port, const uint8_t *data,
                          size_t length, uint32_t timeout_ms);
void hub_stm32_watchdog_feed(HubStm32Port *port);
void hub_stm32_dwt_init(void);
uint32_t hub_stm32_dwt_cycles(void);
uint32_t hub_stm32_cycles_to_us(uint32_t cycles);

#endif
