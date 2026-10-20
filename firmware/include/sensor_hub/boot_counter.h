#ifndef SENSOR_HUB_BOOT_COUNTER_H
#define SENSOR_HUB_BOOT_COUNTER_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Append-only, 5-word records. Data and complements precede the commit word.
 * Never erases storage. A full/corrupt journal fails closed. Call before UART.
 * Callbacks must synchronously read/program durable 32-bit words. */
typedef struct {
    void *context;
    size_t word_count;
    uint32_t (*read)(void *context, size_t word);
    bool (*program)(void *context, size_t word, uint32_t value);
} HubBootStorage;
bool hub_boot_counter_next(const HubBootStorage *storage, uint64_t *boot_id);
#endif
