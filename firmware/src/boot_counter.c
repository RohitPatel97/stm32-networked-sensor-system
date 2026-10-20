#include "sensor_hub/boot_counter.h"
#define COMMIT 0x48425542UL
#define WORDS 5U

bool hub_boot_counter_next(const HubBootStorage *s, uint64_t *boot_id) {
    size_t slot;
    size_t next = 0U;
    uint64_t maximum = 0U;
    uint32_t record[WORDS];
    size_t i;
    if (s == NULL || boot_id == NULL || s->read == NULL || s->program == NULL) {
        return false;
    }
    for (slot = 0U; slot + WORDS <= s->word_count; slot += WORDS) {
        bool used = false;
        for (i = 0U; i < WORDS; ++i) {
            record[i] = s->read(s->context, slot + i);
            used = used || record[i] != UINT32_MAX;
        }
        if (used) { next = slot + WORDS; }
        if (used && record[2] == ~record[0] && record[3] == ~record[1]) {
            uint64_t counter = ((uint64_t)record[1] << 32U) | record[0];
            if (counter <= maximum) { return false; }
            /* Reserve complete data even if power failed before/during commit.
             * Gaps are harmless; a damaged commit must never roll identity back. */
            maximum = counter;
        } else if (record[4] != UINT32_MAX) {
            /* Commit programming happens only after complete, verified data. */
            return false;
        }
    }
    if (next + WORDS > s->word_count || maximum == UINT64_MAX) { return false; }
    ++maximum;
    record[0] = (uint32_t)maximum;
    record[1] = (uint32_t)(maximum >> 32U);
    record[2] = ~record[0];
    record[3] = ~record[1];
    record[4] = COMMIT;
    for (i = 0U; i < WORDS; ++i) {
        if (!s->program(s->context, next + i, record[i]) ||
            s->read(s->context, next + i) != record[i]) { return false; }
    }
    *boot_id = maximum;
    return true;
}
