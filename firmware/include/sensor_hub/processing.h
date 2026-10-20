#ifndef SENSOR_HUB_PROCESSING_H
#define SENSOR_HUB_PROCESSING_H

#include "sensor_hub/types.h"

void hub_process_sample(const HubRawSample *raw, HubProcessedSample *processed);

#endif
