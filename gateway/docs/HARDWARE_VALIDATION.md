# Physical validation checklist

Software validation is complete only for the configurations and evidence recorded in WORKFLOW.md and the host soak report. Physical STM32 UART and Raspberry Pi acceptance remains **unverified**.

On October 12, 2026, the public networked gateway repository had one branch, no releases, and no available Actions artifacts. The public FreeRTOS repository's main and fault-recovery branches contained example telemetry and host validation documentation, but no identified physical capture bundle. Its [pinned validation checklist](https://github.com/RohitPatel97/freertos-sensor-hub/blob/79dec62d2bb11132fa317f2c7f36baa719bd74fd/docs/VALIDATION.md) also marks physical bring-up unverified. This audit cannot rule out private, expired, or unpublished evidence.

## Evidence required before claiming target validation

- [ ] Record MCU board/revision, firmware commit, sensor part numbers, UART pins/voltage, adapter, Pi model, OS image and gateway commit.
- [ ] Preserve a raw UART capture with timestamps and acquisition commands. Label simulator/reference fixtures separately.
- [ ] Validate the capture, retaining accepted/rejected counts and diagnostic lines. If using the FreeRTOS converter, document known boot boundaries and assigned device/boot IDs.
- [ ] Verify normal REST history and WebSocket telemetry against the physical readings.
- [ ] Disconnect/reconnect UART, including interruption mid-frame. Verify first complete recovery frame, unchanged session baseline, counts, and service logs.
- [ ] Reboot the MCU and verify the documented v1 or v2 sequence/session policy, then restart the gateway and confirm its in-memory reset behavior.
- [ ] Install the supplied systemd service, reboot the Pi, and confirm service start, permissions, logs and restart behavior.
- [ ] Specify soak duration, input rate, device count, threshold criteria and measurement tools before running. Record process RSS, CPU, latency, reconnects, loss counters and temperature/fault behavior.

Store each run in a dated directory with raw captures, commands, logs, configuration and a signed-off result summary. A host test, mocked UART test, CI container runtime check or host soak must not be described as a physical target run.
