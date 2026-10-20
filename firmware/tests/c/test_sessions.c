#include "sensor_hub/boot_counter.h"
#include "sensor_hub/telemetry.h"
#include "sensor_hub/config.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); exit(1); } } while (0)
typedef struct { uint32_t words[40]; int fail_at; int writes; bool torn; } Memory;
static uint32_t read_word(void *context, size_t word) { return ((Memory *)context)->words[word]; }
static bool write_word(void *context, size_t word, uint32_t value) {
    Memory *m = context;
    CHECK(m->words[word] == UINT32_MAX);
    m->words[word] = (m->writes == m->fail_at && m->torn) ? value | 0x80000000U : value;
    return m->writes++ != m->fail_at;
}
static void reset(Memory *m) { memset(m->words, 0xff, sizeof(m->words)); m->fail_at = -1; m->writes = 0; m->torn = false; }

/* Model all 6553 slots in the target's reserved flash sector. */
typedef struct { uint32_t *words; unsigned int writes; int fail_at; } FullJournal;
static uint32_t full_read(void *context, size_t word) {
    CHECK(word < 6553U * 5U);
    return ((FullJournal *)context)->words[word];
}
static bool full_program(void *context, size_t word, uint32_t value) {
    FullJournal *m = context;
    CHECK(word < 6553U * 5U && m->words[word] == UINT32_MAX);
    m->words[word] = value;
    return (int)m->writes++ != m->fail_at;
}
static void stress_full_journal(void) {
    FullJournal memory;
    HubBootStorage storage = {&memory, 6553U * 5U, full_read, full_program};
    uint64_t id = 0U, last_published = 0U;
    unsigned int attempt, interrupted = 0U, published = 0U;
    memory.words = malloc(6553U * 5U * sizeof(uint32_t));
    CHECK(memory.words != NULL);
    memset(memory.words, 0xff, 6553U * 5U * sizeof(uint32_t));
    for (attempt = 0U; attempt < 6553U; ++attempt) {
        memory.writes = 0U;
        memory.fail_at = attempt % 13U == 0U ? (int)((attempt / 13U) % 5U) : -1;
        if (hub_boot_counter_next(&storage, &id)) {
            CHECK(memory.fail_at == -1 && id > last_published);
            last_published = id;
            ++published;
        } else {
            CHECK(memory.fail_at >= 0 && id == last_published);
            CHECK(memory.writes == (unsigned int)memory.fail_at + 1U);
            ++interrupted;
        }
    }
    memory.writes = 0U;
    memory.fail_at = -1;
    CHECK(!hub_boot_counter_next(&storage, &id));
    CHECK(memory.writes == 0U && id == last_published);
    CHECK(published == 6048U && interrupted == 505U);
    free(memory.words);
    puts("Full journal: 6553 slots, 6048 published boots, 505 interrupted writes; no identity reuse; exhaustion failed closed");
}

int main(void) {
    Memory memory;
    HubBootStorage storage = {&memory, 40U, read_word, write_word};
    uint64_t id = 0;
    int failure;
    for (failure = 0; failure < 5; ++failure) {
        reset(&memory); memory.fail_at = failure;
        CHECK(!hub_boot_counter_next(&storage, &id));
        memory.fail_at = -1;
        CHECK(hub_boot_counter_next(&storage, &id));
        CHECK(id == (failure >= 2 ? 2U : 1U));
        CHECK(hub_boot_counter_next(&storage, &id));
        CHECK(id == (failure >= 2 ? 3U : 2U));
    }
    reset(&memory); memory.fail_at = 4; memory.torn = true;
    CHECK(!hub_boot_counter_next(&storage, &id));
    memory.fail_at = -1;
    CHECK(hub_boot_counter_next(&storage, &id) && id == 2U);
    reset(&memory);
    CHECK(hub_boot_counter_next(&storage, &id) && id == 1U);
    memory.words[2] ^= 1U;
    CHECK(!hub_boot_counter_next(&storage, &id));
    reset(&memory); storage.word_count = 10U;
    CHECK(hub_boot_counter_next(&storage, &id) && id == 1U);
    CHECK(hub_boot_counter_next(&storage, &id) && id == 2U);
    CHECK(!hub_boot_counter_next(&storage, &id));
    reset(&memory); storage.word_count = 40U;
    memory.words[0] = memory.words[1] = UINT32_MAX;
    memory.words[2] = memory.words[3] = 0U; memory.words[4] = 0x48425542UL;
    CHECK(!hub_boot_counter_next(&storage, &id));
    {
        HubProcessedSample sample = {0};
        HubStatus status = {0};
        HubCounters counters = {0};
        HubTelemetryIdentity identity = {"hub-test", UINT64_MAX, UINT32_MAX, 100U, false, false, 0U};
        char frame[HUB_TELEMETRY_BUFFER_SIZE];
        int count = hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, &identity);
        CHECK(count > 0 && strstr(frame, "\"schema_version\":3") != NULL);
        CHECK(strstr(frame, "18446744073709551615") != NULL);
        CHECK(strstr(frame, "\"sample_present\":false") != NULL);
        CHECK(hub_telemetry_format_v3(frame, (size_t)count, &sample, &status, &counters, &identity) < 0);
        CHECK(frame[0] == '\0');
        identity.device_id = "invalid\"id";
        CHECK(hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, &identity) < 0);
        identity.device_id = "valid"; sample.sequence = 1U;
        CHECK(hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, &identity) < 0);
        identity.sample_present = true; sample.timestamp_us = 101000U;
        CHECK(hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, &identity) < 0);
        CHECK(hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, NULL) < 0);
        identity.uptime_ms = UINT64_MAX; sample.timestamp_us = UINT64_MAX;
        CHECK(hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, &identity) > 0);
        CHECK(strstr(frame, "\"boot_id\":18446744073709551615,") != NULL);
        CHECK(strstr(frame, "\"uptime_ms\":18446744073709551615,") != NULL);
        CHECK(strstr(frame, "\"ts_us\":18446744073709551615,") != NULL);
        CHECK(hub_telemetry_format(frame, sizeof(frame), &sample, &status, &counters) > 0);
        CHECK(strstr(frame, "\"ts_us\":18446744073709551615,") != NULL);
        identity.boot_id = 0U; identity.uptime_ms = 0U; sample.timestamp_us = 0U;
        count = hub_telemetry_format_v3(frame, sizeof(frame), &sample, &status, &counters, &identity);
        CHECK(count > 0 && strstr(frame, "\"boot_id\":0,") != NULL);
        CHECK(strstr(frame, "\"uptime_ms\":0,") != NULL && strstr(frame, "\"ts_us\":0,") != NULL);
        CHECK(hub_telemetry_format_v3(frame, (size_t)count + 1U, &sample, &status, &counters, &identity) == count);
        /* Every truncation point, including each decimal digit, fails closed. */
        for (size_t capacity = 1U; capacity <= (size_t)count; ++capacity) {
            CHECK(hub_telemetry_format_v3(frame, capacity, &sample, &status, &counters, &identity) < 0);
            CHECK(frame[0] == '\0');
        }
    }
    puts("Boot journal interruption, torn commit, corruption, exhaustion, and v3 formatter checks passed");
    stress_full_journal();
    return 0;
}
