#include "sensor_hub/bmp280.h"

#define BMP280_REG_CALIB    0x88U
#define BMP280_REG_CHIP_ID  0xD0U
#define BMP280_REG_CTRL     0xF4U
#define BMP280_REG_CONFIG   0xF5U
#define BMP280_REG_DATA     0xF7U

static uint16_t le_u16(const uint8_t *bytes) {
    return (uint16_t)((uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8U));
}

static int16_t le_i16(const uint8_t *bytes) {
    return (int16_t)le_u16(bytes);
}

bool hub_bmp280_init(HubBmp280 *sensor, HubI2cBus *bus, uint8_t address) {
    uint8_t identity = 0U;
    uint8_t calibration[24];
    uint8_t value;

    if (sensor == NULL || bus == NULL) {
        return false;
    }
    sensor->bus = bus;
    sensor->address = address;
    sensor->initialized = false;
    if (!hub_i2c_read(bus, address, BMP280_REG_CHIP_ID, &identity, 1U, 5U) ||
        identity != 0x58U) {
        return false;
    }
    if (!hub_i2c_read(bus, address, BMP280_REG_CALIB,
                      calibration, sizeof(calibration), 5U)) {
        return false;
    }

    sensor->dig_t1 = le_u16(&calibration[0]);
    sensor->dig_t2 = le_i16(&calibration[2]);
    sensor->dig_t3 = le_i16(&calibration[4]);
    sensor->dig_p1 = le_u16(&calibration[6]);
    sensor->dig_p2 = le_i16(&calibration[8]);
    sensor->dig_p3 = le_i16(&calibration[10]);
    sensor->dig_p4 = le_i16(&calibration[12]);
    sensor->dig_p5 = le_i16(&calibration[14]);
    sensor->dig_p6 = le_i16(&calibration[16]);
    sensor->dig_p7 = le_i16(&calibration[18]);
    sensor->dig_p8 = le_i16(&calibration[20]);
    sensor->dig_p9 = le_i16(&calibration[22]);
    if (sensor->dig_p1 == 0U) {
        return false;
    }

    value = 0x00U; /* 0.5 ms standby, filter disabled. */
    if (!hub_i2c_write(bus, address, BMP280_REG_CONFIG, &value, 1U, 5U)) {
        return false;
    }
    /* temp x1 + pressure x2 + normal mode: ~9.2 ms conversion cadence. */
    value = 0x2BU;
    sensor->initialized = hub_i2c_write(bus, address, BMP280_REG_CTRL,
                                       &value, 1U, 5U);
    return sensor->initialized;
}

/* Defined floor division replaces implementation-defined signed right shifts. */
static int64_t shift_right(int64_t value, unsigned int bits) {
    const int64_t divisor = (int64_t)1 << bits;
    return value / divisor - (value < 0 && value % divisor != 0);
}

static bool multiply(int64_t a, int64_t b, int64_t *result) {
    if ((a > 0 && ((b > 0 && a > INT64_MAX / b) ||
                   (b < 0 && b < INT64_MIN / a))) ||
        (a < 0 && ((b > 0 && a < INT64_MIN / b) ||
                   (b < 0 && a < INT64_MAX / b)))) {
        return false;
    }
    *result = a * b;
    return true;
}

static bool add(int64_t a, int64_t b, int64_t *result) {
    if ((b > 0 && a > INT64_MAX - b) ||
        (b < 0 && a < INT64_MIN - b)) {
        return false;
    }
    *result = a + b;
    return true;
}

bool hub_bmp280_compensate(HubBmp280 *sensor, int32_t adc_t,
                           int32_t adc_p, HubBmp280Reading *reading) {
    int64_t var1;
    int64_t var2;
    int64_t pvar1;
    int64_t pvar2;
    int64_t pressure;
    int64_t term;
    int64_t temperature;

    if (sensor == NULL || reading == NULL || sensor->dig_p1 == 0U ||
        adc_t < 0 || adc_t > 0xFFFFF || adc_p < 0 || adc_p > 0xFFFFF ||
        adc_t == 0x80000 || adc_p == 0x80000) {
        return false;
    }

    /* Temperature intermediates fit int64 for every 20-bit ADC/16-bit trim. */
    var1 = shift_right(((int64_t)(adc_t >> 3) - sensor->dig_t1 * 2) *
                       sensor->dig_t2, 11U);
    term = (int64_t)(adc_t >> 4) - sensor->dig_t1;
    var2 = shift_right(shift_right(term * term, 12U) * sensor->dig_t3, 14U);
    sensor->t_fine = (int32_t)(var1 + var2);
    temperature = shift_right((int64_t)sensor->t_fine * 5 + 128, 8U);

    pvar1 = (int64_t)sensor->t_fine - 128000;
    /* These products fit int64; the later pressure path is explicitly checked
       so corrupt calibration cannot trigger signed-overflow undefined behavior. */
    pvar2 = pvar1 * pvar1 * sensor->dig_p6;
    pvar2 += pvar1 * sensor->dig_p5 * 131072;
    pvar2 += (int64_t)sensor->dig_p4 * 34359738368LL;
    pvar1 = shift_right(pvar1 * pvar1 * sensor->dig_p3, 8U) +
            pvar1 * sensor->dig_p2 * 4096;
    if (!multiply(140737488355328LL + pvar1, sensor->dig_p1, &pvar1)) {
        return false;
    }
    pvar1 = shift_right(pvar1, 33U);
    if (pvar1 <= 0) {
        return false;
    }
    pressure = 1048576 - adc_p;
    if (!multiply(pressure * 2147483648LL - pvar2, 3125, &pressure)) {
        return false;
    }
    pressure /= pvar1;
    term = shift_right(pressure, 13U);
    if (!multiply(term, term, &pvar1) ||
        !multiply(pvar1, sensor->dig_p9, &pvar1) ||
        !multiply(sensor->dig_p8, pressure, &pvar2)) {
        return false;
    }
    pvar1 = shift_right(pvar1, 25U);
    pvar2 = shift_right(pvar2, 19U);
    if (!add(pressure, pvar1, &pressure) || !add(pressure, pvar2, &pressure)) {
        return false;
    }
    pressure = shift_right(pressure, 8U) + (int64_t)sensor->dig_p7 * 16;
    pressure = shift_right(pressure + 128, 8U);
    if (pressure <= 0 || pressure > UINT32_MAX) {
        return false;
    }
    reading->temperature_centi_c = (int32_t)temperature;
    reading->pressure_pa = (uint32_t)pressure;
    return true;
}

bool hub_bmp280_read(HubBmp280 *sensor, HubBmp280Reading *reading) {
    uint8_t raw[6];
    int32_t adc_p;
    int32_t adc_t;

    if (sensor == NULL || sensor->bus == NULL || reading == NULL) {
        return false;
    }
    if (!sensor->initialized &&
        !hub_bmp280_init(sensor, sensor->bus, sensor->address)) {
        return false;
    }
    if (!hub_i2c_read(sensor->bus, sensor->address, BMP280_REG_DATA,
                      raw, sizeof(raw), 5U)) {
        sensor->initialized = false;
        return false;
    }
    adc_p = ((int32_t)raw[0] << 12) | ((int32_t)raw[1] << 4) |
            ((int32_t)raw[2] >> 4);
    adc_t = ((int32_t)raw[3] << 12) | ((int32_t)raw[4] << 4) |
            ((int32_t)raw[5] >> 4);
    return hub_bmp280_compensate(sensor, adc_t, adc_p, reading);
}
