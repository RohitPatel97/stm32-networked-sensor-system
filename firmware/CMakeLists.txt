cmake_minimum_required(VERSION 3.20)
project(freertos_sensor_hub VERSION 1.0.0 LANGUAGES C)

option(HUB_ENABLE_STRICT_WARNINGS "Treat compiler warnings as errors" ON)
option(HUB_BUILD_STM32 "Build the complete STM32F401RE firmware with Arm GNU" OFF)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(sensor_hub_core STATIC
    src/boot_counter.c
    src/bmp280.c
    src/health.c
    src/i2c_bus.c
    src/mpu6050.c
    src/processing.c
    src/sensor_state.c
    src/telemetry.c
)
target_include_directories(sensor_hub_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(sensor_hub_core PUBLIC c_std_99)

if(HUB_ENABLE_STRICT_WARNINGS)
    if(MSVC)
        target_compile_options(sensor_hub_core PRIVATE /W4 /WX)
    elseif(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(sensor_hub_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
    endif()
endif()

if(NOT MSVC)
    target_link_libraries(sensor_hub_core PUBLIC m)
endif()

if(HUB_BUILD_STM32)
    if(NOT CMAKE_CROSSCOMPILING)
        message(FATAL_ERROR "STM32 requires -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake")
    endif()
    add_subdirectory(target/stm32f401re)
    return()
endif()

add_executable(sensor_hub_sim sim/host_sim.c)
target_link_libraries(sensor_hub_sim PRIVATE sensor_hub_core)
if(HUB_ENABLE_STRICT_WARNINGS AND CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(sensor_hub_sim PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(session_tests tests/c/test_sessions.c)
    target_link_libraries(session_tests PRIVATE sensor_hub_core)
    add_test(NAME boot_and_protocol_tests COMMAND session_tests)
    add_executable(core_tests tests/c/test_core.c)
    target_link_libraries(core_tests PRIVATE sensor_hub_core)
    if(HUB_ENABLE_STRICT_WARNINGS AND CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(core_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
    endif()
    add_test(NAME core_unit_tests COMMAND core_tests)

    find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
        add_test(NAME simulator_integration_tests
                 COMMAND ${Python3_EXECUTABLE}
                         ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_sim_cli.py
                         $<TARGET_FILE:sensor_hub_sim>)
        add_test(NAME python_tool_tests
                 COMMAND ${Python3_EXECUTABLE} -m unittest discover
                         -s tests -p "test_*.py" -v)
        set_tests_properties(python_tool_tests PROPERTIES
                             WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
    endif()
endif()
