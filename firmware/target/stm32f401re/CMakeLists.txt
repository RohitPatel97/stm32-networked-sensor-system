include(FetchContent)
enable_language(ASM)
find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)

option(HUB_ENABLE_WATCHDOG "Start the hardware watchdog (enable after board bring-up)" OFF)
set(HUB_DEVICE_EPOCH "0" CACHE STRING "Provisioning epoch: change after erasing boot journal")
string(LENGTH "${HUB_DEVICE_EPOCH}" HUB_DEVICE_EPOCH_LENGTH)
if(NOT HUB_DEVICE_EPOCH MATCHES "^[A-Za-z0-9_-]+$" OR HUB_DEVICE_EPOCH_LENGTH GREATER 24)
    message(FATAL_ERROR "HUB_DEVICE_EPOCH must be 1..24 identifier characters")
endif()

# HAL and device commits are the pair shipped with STM32CubeF4 v1.28.1.
# SOURCE_SUBDIR prevents upstream build systems from choosing a different port.
FetchContent_Declare(stm32_hal
    GIT_REPOSITORY https://github.com/STMicroelectronics/stm32f4xx-hal-driver.git
    GIT_TAG c2e1406d7ea4b73aa42b98ddeb75a8670b1a5a16
    SOURCE_SUBDIR hub-unused)
FetchContent_Declare(stm32_device
    GIT_REPOSITORY https://github.com/STMicroelectronics/cmsis-device-f4.git
    GIT_TAG 5f41fb29d22773896c780052bf61e47fc924d524
    SOURCE_SUBDIR hub-unused)
FetchContent_Declare(cmsis_core
    GIT_REPOSITORY https://github.com/ARM-software/CMSIS_5.git
    GIT_TAG 61e36449f53c25ef7825c40f7dd93685736f457f
    SOURCE_SUBDIR hub-unused)
FetchContent_Declare(freertos
    GIT_REPOSITORY https://github.com/FreeRTOS/FreeRTOS-Kernel.git
    GIT_TAG f388a5c8078e152913e4eb3c5d75bf89561392df
    SOURCE_SUBDIR hub-unused)
FetchContent_MakeAvailable(stm32_hal stm32_device cmsis_core freertos)
configure_file(${stm32_hal_SOURCE_DIR}/LICENSE.md licenses/STM32-HAL-LICENSE.md COPYONLY)
configure_file(${stm32_device_SOURCE_DIR}/LICENSE.md licenses/STM32-CMSIS-LICENSE.md COPYONLY)
configure_file(${cmsis_core_SOURCE_DIR}/LICENSE.txt licenses/CMSIS-Core-LICENSE.txt COPYONLY)
configure_file(${freertos_SOURCE_DIR}/LICENSE.md licenses/FreeRTOS-LICENSE.md COPYONLY)
configure_file(${PROJECT_SOURCE_DIR}/LICENSE licenses/Sensor-Hub-LICENSE.txt COPYONLY)

add_library(board_config INTERFACE)
target_compile_definitions(board_config INTERFACE STM32F401xE USE_HAL_DRIVER
    HUB_DEVICE_EPOCH="${HUB_DEVICE_EPOCH}"
    HUB_ENABLE_WATCHDOG=$<BOOL:${HUB_ENABLE_WATCHDOG}>)
target_include_directories(board_config INTERFACE Core/Inc)
target_include_directories(board_config SYSTEM INTERFACE
    ${stm32_hal_SOURCE_DIR}/Inc
    ${stm32_device_SOURCE_DIR}/Include
    ${cmsis_core_SOURCE_DIR}/CMSIS/Core/Include
    ${freertos_SOURCE_DIR}/include
    ${freertos_SOURCE_DIR}/portable/GCC/ARM_CM4F)

add_library(board_support STATIC
    ${stm32_device_SOURCE_DIR}/Source/Templates/system_stm32f4xx.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_cortex.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_dma.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_flash.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_flash_ex.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_gpio.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_i2c.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_iwdg.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_pwr.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_pwr_ex.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_rcc.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_rcc_ex.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_tim.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_tim_ex.c
    ${stm32_hal_SOURCE_DIR}/Src/stm32f4xx_hal_uart.c
    ${freertos_SOURCE_DIR}/tasks.c
    ${freertos_SOURCE_DIR}/queue.c
    ${freertos_SOURCE_DIR}/list.c
    ${freertos_SOURCE_DIR}/portable/MemMang/heap_4.c
    ${freertos_SOURCE_DIR}/portable/GCC/ARM_CM4F/port.c)
target_link_libraries(board_support PUBLIC board_config)

add_executable(sensor_hub_firmware
    Core/Src/hub_identity.c
    Core/Src/main.c
    Core/Src/syscalls.c
    Core/Src/app_freertos.c
    Core/Src/hub_stm32_port.c
    ${stm32_device_SOURCE_DIR}/Source/Templates/gcc/startup_stm32f401xe.s)
set_target_properties(sensor_hub_firmware PROPERTIES SUFFIX ".elf")
target_compile_options(sensor_hub_firmware PRIVATE
    $<$<COMPILE_LANGUAGE:C>:-Wall;-Wextra;-Wpedantic;-Werror;-fstack-usage>)
target_compile_options(sensor_hub_core PRIVATE -fstack-usage)
target_link_libraries(sensor_hub_firmware PRIVATE sensor_hub_core board_support)
set(HUB_LINKER_SCRIPT ${CMAKE_CURRENT_SOURCE_DIR}/STM32F401RE.ld)
target_link_options(sensor_hub_firmware PRIVATE
    -T${HUB_LINKER_SCRIPT} -Wl,--gc-sections -Wl,--print-memory-usage
    -Wl,-Map=${CMAKE_CURRENT_BINARY_DIR}/sensor_hub_firmware.map
    -u _printf_float)
set_property(TARGET sensor_hub_firmware APPEND PROPERTY LINK_DEPENDS
    ${HUB_LINKER_SCRIPT} ${PROJECT_SOURCE_DIR}/tools/check_firmware.py)
add_custom_command(TARGET sensor_hub_firmware POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:sensor_hub_firmware>
            ${CMAKE_CURRENT_BINARY_DIR}/sensor_hub_firmware.hex
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:sensor_hub_firmware>
            ${CMAKE_CURRENT_BINARY_DIR}/sensor_hub_firmware.bin
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:sensor_hub_firmware>
    COMMAND ${Python3_EXECUTABLE} ${PROJECT_SOURCE_DIR}/tools/check_firmware.py
            --elf $<TARGET_FILE:sensor_hub_firmware>
            --bin ${CMAKE_CURRENT_BINARY_DIR}/sensor_hub_firmware.bin
            --nm ${CMAKE_NM}
    VERBATIM)
