#include "main.h"
#include "app_freertos.h"
#include "hub_identity.h"
#include "FreeRTOS.h"
#include "task.h"

I2C_HandleTypeDef hi2c1;
UART_HandleTypeDef huart2;
IWDG_HandleTypeDef hiwdg;
TIM_HandleTypeDef htim2;

/* Inspect in a debugger; captured before flags are cleared. */
volatile uint32_t hub_reset_flags;
static volatile uint32_t hal_milliseconds;

static void clock_init(void) {
    RCC_OscInitTypeDef oscillator = {0};
    RCC_ClkInitTypeDef clocks = {0};
    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);
    oscillator.OscillatorType = RCC_OSCILLATORTYPE_HSI;
    oscillator.HSIState = RCC_HSI_ON;
    oscillator.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    oscillator.PLL.PLLState = RCC_PLL_ON;
    oscillator.PLL.PLLSource = RCC_PLLSOURCE_HSI;
    oscillator.PLL.PLLM = 16U;
    oscillator.PLL.PLLN = 336U;
    oscillator.PLL.PLLP = RCC_PLLP_DIV4;
    oscillator.PLL.PLLQ = 7U;
    if (HAL_RCC_OscConfig(&oscillator) != HAL_OK) { Error_Handler(); }
    clocks.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                       RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clocks.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clocks.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clocks.APB1CLKDivider = RCC_HCLK_DIV2;
    clocks.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&clocks, FLASH_LATENCY_2) != HAL_OK) { Error_Handler(); }
}

static void peripheral_init(void) {
    GPIO_InitTypeDef gpio = {0};
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_I2C1_CLK_ENABLE();
    __HAL_RCC_USART2_CLK_ENABLE();
    __HAL_RCC_TIM2_CLK_ENABLE();

    gpio.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    gpio.Mode = GPIO_MODE_AF_OD;
    gpio.Pull = GPIO_NOPULL; /* External 4.7 kOhm pull-ups required. */
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Alternate = GPIO_AF4_I2C1;
    HAL_GPIO_Init(GPIOB, &gpio);
    gpio.Pin = GPIO_PIN_2 | GPIO_PIN_3;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_PULLUP;
    gpio.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &gpio);

    hi2c1.Instance = I2C1;
    hi2c1.Init.ClockSpeed = 400000U;
    hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&hi2c1) != HAL_OK) { Error_Handler(); }

    huart2.Instance = USART2;
    huart2.Init.BaudRate = 115200U;
    huart2.Init.WordLength = UART_WORDLENGTH_8B;
    huart2.Init.StopBits = UART_STOPBITS_1;
    huart2.Init.Parity = UART_PARITY_NONE;
    huart2.Init.Mode = UART_MODE_TX_RX;
    huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart2.Init.OverSampling = UART_OVERSAMPLING_16;
    if (HAL_UART_Init(&huart2) != HAL_OK) { Error_Handler(); }

    htim2.Instance = TIM2;
    htim2.Init.Prescaler = 8399U; /* 84 MHz / 8400 / 100 = 100 Hz. */
    htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim2.Init.Period = 99U;
    htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    if (HAL_TIM_Base_Init(&htim2) != HAL_OK) { Error_Handler(); }
    HAL_NVIC_SetPriority(TIM2_IRQn, configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY, 0U);
    HAL_NVIC_EnableIRQ(TIM2_IRQn);
}

int main(void) {
    SCB->VTOR = FLASH_BASE;
    __DSB();
    __ISB();
    hub_reset_flags = RCC->CSR;
    __HAL_RCC_CLEAR_RESET_FLAGS();
    HAL_Init();
    clock_init();
    hub_identity_init(); /* Persist identity before starting tasks or telemetry. */
    peripheral_init();
    HubRtos_Init();
#if HUB_ENABLE_WATCHDOG
    hiwdg.Instance = IWDG;
    hiwdg.Init.Prescaler = IWDG_PRESCALER_32;
    hiwdg.Init.Reload = 1999U; /* Nominal 2 s; actual LSI frequency varies. */
    if (HAL_IWDG_Init(&hiwdg) != HAL_OK) { Error_Handler(); }
#endif
    vTaskStartScheduler();
    Error_Handler();
}

/* Separate HAL timeout clock: SysTick belongs to FreeRTOS once it starts.
 * Do not call blocking HAL routines from an ISR or a critical section. */
uint32_t HAL_GetTick(void) { return hal_milliseconds; }

void SysTick_Handler(void) {
    extern void xPortSysTickHandler(void);
    ++hal_milliseconds;
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
        xPortSysTickHandler();
    }
}

void TIM2_IRQHandler(void) { HAL_TIM_IRQHandler(&htim2); }
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *timer) {
    HubRtos_TimerPeriodElapsedFromISR(timer);
}

void vApplicationMallocFailedHook(void) { Error_Handler(); }
void vApplicationStackOverflowHook(TaskHandle_t task, char *name) {
    (void)task;
    (void)name;
    Error_Handler();
}

void Error_Handler(void) {
    __disable_irq();
    for (;;) { /* Halt in debugger, or let an enabled IWDG reset the MCU. */ }
}
