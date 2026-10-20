#include "hub_identity.h"
#include "main.h"
#include "sensor_hub/boot_counter.h"
#include <stdio.h>

/* F401RE sector 7, excluded from the image by the linker script. */
#define JOURNAL_ADDRESS 0x08060000UL
#define JOURNAL_BYTES (128U * 1024U)
char hub_device_id[64];
uint64_t hub_boot_id;

static uint32_t read_word(void *context, size_t index) {
    (void)context;
    return ((volatile const uint32_t *)JOURNAL_ADDRESS)[index];
}
static bool program_word(void *context, size_t index, uint32_t value) {
    HAL_StatusTypeDef result;
    (void)context;
    result = HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD,
            JOURNAL_ADDRESS + (uint32_t)index * 4U, value);
    /* Journal scanning may cache erased words. Verify actual flash after writes. */
    FLASH_FlushCaches();
    return result == HAL_OK;
}
void hub_identity_init(void) {
    const HubBootStorage storage = {NULL, JOURNAL_BYTES / 4U, read_word, program_word};
    bool ok;
    (void)snprintf(hub_device_id, sizeof(hub_device_id), "stm32-%08lx%08lx%08lx-%s",
                   (unsigned long)HAL_GetUIDw0(), (unsigned long)HAL_GetUIDw1(),
                   (unsigned long)HAL_GetUIDw2(), HUB_DEVICE_EPOCH);
    if (HAL_FLASH_Unlock() != HAL_OK) { Error_Handler(); }
    ok = hub_boot_counter_next(&storage, &hub_boot_id);
    (void)HAL_FLASH_Lock();
    if (!ok) { Error_Handler(); }
}
