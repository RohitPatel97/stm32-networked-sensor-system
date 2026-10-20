#include "app_freertos.h"

#include <string.h>

#include "FreeRTOS.h"
#include "queue.h"
#include "semphr.h"
#include "task.h"

#include "hub_stm32_port.h"
#include "hub_identity.h"
#include "main.h"
#include "sensor_hub/bmp280.h"
#include "sensor_hub/config.h"
#include "sensor_hub/health.h"
#include "sensor_hub/mpu6050.h"
#include "sensor_hub/processing.h"
#include "sensor_hub/sensor_state.h"
#include "sensor_hub/telemetry.h"

/* Supplied by main.c, or by the peripheral setup in a CubeMX integration. */
extern I2C_HandleTypeDef hi2c1;
extern UART_HandleTypeDef huart2;
extern IWDG_HandleTypeDef hiwdg;
extern TIM_HandleTypeDef htim2;

#define RAW_QUEUE_LENGTH 8U
#define SENSOR_TASK_PRIORITY   (tskIDLE_PRIORITY + 4U)
#define PROCESS_TASK_PRIORITY  (tskIDLE_PRIORITY + 3U)
#define TELEMETRY_TASK_PRIORITY (tskIDLE_PRIORITY + 2U)
#define HEALTH_TASK_PRIORITY   (tskIDLE_PRIORITY + 1U)

static SemaphoreHandle_t sample_tick_semaphore;
static SemaphoreHandle_t i2c_mutex;
static QueueHandle_t raw_queue;
static QueueHandle_t processed_queue;

static HubStm32Port port;
static HubI2cBus bus;
static HubMpu6050 mpu6050;
static HubBmp280 bmp280;
static HubHealthMonitor health;
static HubCounters counters;
static HubStatus status;
static TaskHandle_t task_handles[HUB_TASK_COUNT];
extern volatile uint32_t hub_reset_flags;

/* Shared extended RTOS clock: survive the 32-bit millisecond tick rollover. */
static uint64_t uptime_ms(void) {
    static uint32_t last_tick;
    static uint64_t upper;
    uint32_t now;
    uint64_t result;
    taskENTER_CRITICAL();
    now = (uint32_t)xTaskGetTickCount();
    if (now < last_tick) { upper += (1ULL << 32U); }
    last_tick = now;
    result = upper + now;
    taskEXIT_CRITICAL();
    return result;
}
/* Debugger-visible stack minima, in StackType_t words, refreshed at 1 Hz. */
volatile uint32_t hub_stack_high_water_words[HUB_TASK_COUNT];

static void heartbeat(HubTaskId task) {
    taskENTER_CRITICAL();
    hub_health_heartbeat(&health, task, (uint32_t)xTaskGetTickCount());
    taskEXIT_CRITICAL();
}

static void sensor_task(void *argument) {
    uint32_t sequence = 0U;
    HAL_StatusTypeDef timer_status;
    (void)argument;
    status.mpu6050_online = hub_mpu6050_init(&mpu6050, &bus, HUB_MPU6050_ADDRESS);
    status.bmp280_online = hub_bmp280_init(&bmp280, &bus, HUB_BMP280_ADDRESS);
    timer_status = HAL_TIM_Base_Start_IT(&htim2);
    if (timer_status != HAL_OK) {
        Error_Handler();
    }

    for (;;) {
        HubRawSample raw;
        HubMpu6050Reading imu;
        HubBmp280Reading baro;
        uint32_t started;
        TickType_t started_tick;
        bool mpu_ok;
        bool bmp_ok;

        (void)xSemaphoreTake(sample_tick_semaphore, portMAX_DELAY);
        started_tick = xTaskGetTickCount();
        started = hub_stm32_dwt_cycles();
        memset(&raw, 0, sizeof(raw));
        raw.sequence = sequence++;
        raw.timestamp_us = uptime_ms() * 1000ULL;
        mpu_ok = hub_mpu6050_read(&mpu6050, &imu);
        bmp_ok = hub_bmp280_read(&bmp280, &baro);
        hub_sensor_state_update(mpu_ok, &status.mpu6050_online,
                                 &status.mpu6050_consecutive_errors, &counters);
        hub_sensor_state_update(bmp_ok, &status.bmp280_online,
                                 &status.bmp280_consecutive_errors, &counters);
        if (mpu_ok) {
            memcpy(raw.accel_raw, imu.accel, sizeof(raw.accel_raw));
            memcpy(raw.gyro_raw, imu.gyro, sizeof(raw.gyro_raw));
            raw.imu_temp_raw = imu.temperature;
            raw.valid_mask |= HUB_MPU6050_VALID;
        }
        if (bmp_ok) {
            raw.baro_temperature_centi_c = baro.temperature_centi_c;
            raw.pressure_pa = baro.pressure_pa;
            raw.valid_mask |= HUB_BMP280_VALID;
        }
        raw.acquisition_time_us = hub_stm32_cycles_to_us(
            hub_stm32_dwt_cycles() - started);
        counters.samples_requested++;
        counters.samples_acquired++;
        if (raw.acquisition_time_us > counters.max_acquisition_us) {
            counters.max_acquisition_us = raw.acquisition_time_us;
        }
        if (xQueueSend(raw_queue, &raw, 0U) != pdPASS) {
            counters.raw_queue_drops++;
        }
        heartbeat(HUB_TASK_ACQUISITION);
        /* Polling HAL timeouts plus retries can exceed the 10 ms sample period.
         * The next timer semaphore is then already pending on every iteration.
         * Block for one tick so processing, telemetry and health still run during
         * a bus outage; taskYIELD alone would only admit equal-priority tasks. */
        if ((TickType_t)(xTaskGetTickCount() - started_tick) >= pdMS_TO_TICKS(10U)) {
            vTaskDelay(1U);
        }
    }
}

static void processing_task(void *argument) {
    (void)argument;
    for (;;) {
        HubRawSample raw;
        HubProcessedSample processed;
        uint32_t started;
        if (xQueueReceive(raw_queue, &raw, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        started = hub_stm32_dwt_cycles();
        hub_process_sample(&raw, &processed);
        processed.processing_time_us = hub_stm32_cycles_to_us(
            hub_stm32_dwt_cycles() - started);
        counters.samples_processed++;
        if (processed.processing_time_us > counters.max_processing_us) {
            counters.max_processing_us = processed.processing_time_us;
        }
        (void)xQueueOverwrite(processed_queue, &processed);
        heartbeat(HUB_TASK_PROCESSING);
    }
}

static void telemetry_task(void *argument) {
    TickType_t wake = xTaskGetTickCount();
    char frame[HUB_TELEMETRY_BUFFER_SIZE];
    uint32_t frame_sequence = 0U;
    (void)argument;
    for (;;) {
        HubProcessedSample sample = {0};
        HubTelemetryIdentity identity;
        HubCounters snapshot;
        HubStatus status_snapshot;
        int length;
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(100U));
        heartbeat(HUB_TASK_TELEMETRY);
        identity.sample_present = xQueuePeek(processed_queue, &sample, 0U) == pdTRUE;
        identity.device_id = hub_device_id;
        identity.boot_id = hub_boot_id;
        identity.frame_sequence = frame_sequence++;
        identity.uptime_ms = uptime_ms();
        identity.watchdog_enabled = HUB_ENABLE_WATCHDOG != 0;
        identity.reset_flags = hub_reset_flags;
        taskENTER_CRITICAL();
        counters.i2c_transactions = bus.transactions;
        counters.i2c_retries = bus.retries;
        counters.i2c_errors = bus.errors;
        snapshot = counters;
        status_snapshot = status;
        taskEXIT_CRITICAL();
        length = hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status_snapshot,
                                         &snapshot, &identity);
        if (length > 0 &&
            hub_stm32_uart_write(&port, (const uint8_t *)frame,
                                 (size_t)length, 95U)) {
            counters.telemetry_frames++;
        }
    }
}

static void health_task(void *argument) {
    TickType_t wake = xTaskGetTickCount();
    (void)argument;
    for (;;) {
        uint32_t misses = 0U;
        bool healthy;
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(1000U));
        heartbeat(HUB_TASK_HEALTH);
#if INCLUDE_uxTaskGetStackHighWaterMark == 1
        {
            unsigned int task;
            for (task = 0U; task < HUB_TASK_COUNT; ++task) {
                hub_stack_high_water_words[task] =
                    (uint32_t)uxTaskGetStackHighWaterMark(task_handles[task]);
            }
        }
#endif
        taskENTER_CRITICAL();
        healthy = hub_health_all_tasks_alive(&health,
                                              (uint32_t)xTaskGetTickCount(),
                                              &misses);
        status.watchdog_fed = healthy;
        if (healthy) {
            counters.watchdog_feeds++;
        } else {
            counters.watchdog_withheld++;
            counters.deadline_misses += misses;
        }
        taskEXIT_CRITICAL();
        if (healthy) {
            hub_stm32_watchdog_feed(&port);
        }
    }
}

void HubRtos_Init(void) {
    BaseType_t created;
    memset(&counters, 0, sizeof(counters));
    memset(&status, 0, sizeof(status));
    hub_health_init(&health);
    hub_stm32_dwt_init();

    sample_tick_semaphore = xSemaphoreCreateBinary();
    i2c_mutex = xSemaphoreCreateMutex();
    raw_queue = xQueueCreate(RAW_QUEUE_LENGTH, sizeof(HubRawSample));
    processed_queue = xQueueCreate(1U, sizeof(HubProcessedSample));
    if (sample_tick_semaphore == NULL || i2c_mutex == NULL ||
        raw_queue == NULL || processed_queue == NULL) {
        Error_Handler();
    }

    hub_stm32_port_init(&port, &hi2c1, &huart2, &hiwdg, i2c_mutex);
    hub_stm32_bus_init(&bus, &port);
    created = xTaskCreate(sensor_task, "sensor", 384U, NULL,
                          SENSOR_TASK_PRIORITY, &task_handles[HUB_TASK_ACQUISITION]);
    if (created != pdPASS) {
        Error_Handler();
    }
    created = xTaskCreate(processing_task, "process", 512U, NULL,
                          PROCESS_TASK_PRIORITY, &task_handles[HUB_TASK_PROCESSING]);
    if (created != pdPASS) {
        Error_Handler();
    }
    created = xTaskCreate(telemetry_task, "telemetry", 1536U, NULL,
                          TELEMETRY_TASK_PRIORITY, &task_handles[HUB_TASK_TELEMETRY]);
    if (created != pdPASS) {
        Error_Handler();
    }
    created = xTaskCreate(health_task, "health", 256U, NULL,
                          HEALTH_TASK_PRIORITY, &task_handles[HUB_TASK_HEALTH]);
    if (created != pdPASS) {
        Error_Handler();
    }
}

void HubRtos_TimerPeriodElapsedFromISR(TIM_HandleTypeDef *timer) {
    BaseType_t higher_priority_woken = pdFALSE;
    if (timer == &htim2) {
        if (xSemaphoreGiveFromISR(sample_tick_semaphore,
                                  &higher_priority_woken) != pdTRUE) {
            counters.sample_tick_overruns++;
        }
        portYIELD_FROM_ISR(higher_priority_woken);
    }
}
