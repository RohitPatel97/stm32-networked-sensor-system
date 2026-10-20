#ifndef APP_FREERTOS_H
#define APP_FREERTOS_H

#include "stm32f4xx_hal.h"

/* Call after MX_* peripheral initialization and before vTaskStartScheduler(). */
void HubRtos_Init(void);

/* Call from HAL_TIM_PeriodElapsedCallback when the 100 Hz timer elapses. */
void HubRtos_TimerPeriodElapsedFromISR(TIM_HandleTypeDef *timer);

#endif
