# Isolated test run — October 17, 2026

One previously uncovered WebSocket startup error was reproduced, fixed, and
regression-tested. All final checks passed. Both original projects remain
unchanged, with 116 source-file hashes verified and clean original Git trees.

## Isolation and scope

The run used a new source copy in `build/sandbox-20261017-rxotbc23`, a fresh Python
3.12.14 virtual environment, the pinned dependency lock, a newly built gateway
wheel, and a fresh C Debug build. Existing virtual environments, build outputs,
Git metadata, and logs were excluded from the source copy.

Docker was unavailable. This was isolation of project files, dependencies, and
test processes on the Windows host, **not an OS security sandbox or VM**. The
runtime service bound only to an ephemeral localhost port; no serial hardware,
deployment, or external service was used. The temporary server exited and no
sandbox Python/simulator processes remained after cleanup.

## Confirmed error and fix

Previously, `/ws/telemetry` sent the initial snapshot before registering the
client's telemetry subscription. An asynchronous socket send can yield while
the producer accepts a new frame. That frame then appears in neither the
already-captured snapshot nor the client's event queue.

A new test forces a real parsed C frame through the gateway during that initial
send. It failed before the fix: the hub accepted the frame, but the socket never
received it. The handler now registers its subscriber before capturing and
sending the snapshot. The regression passes, and a second case verifies that a
disconnect during snapshot delivery removes the subscription cleanly.

The verified application fix and two regression cases were copied into the
combined project. The 109 tested source files checked against the combined tree
match byte for byte. Neither original project was edited.

Changed implementation: `gateway/src/sensor_monitor/app.py`.
Added tests: `tests/integration/test_websocket_startup.py`.

## Final results

| Check | Result |
| --- | --- |
| Python suite | **251 passed**: 198 inherited gateway cases and 53 integration cases |
| C firmware suites | **4/4 passed**, using a fresh Debug build |
| C warning policy | `-Wall -Wextra -Wpedantic -Werror` build passed |
| Undefined-behavior instrumentation | `-fsanitize=undefined -fno-sanitize-recover=all`; no project test failures |
| Instrumentation positive control | Deliberately overflowing throwaway C program was detected and exited with status 3, as expected |
| Seeded corrupted-stream checks | **5,000/5,000** cases recovered the valid trailing C frame; decoder buffer stayed at or below 2,048 bytes |
| Corrupt records rejected | 21,560 rejection events across those stream cases; no unhandled decoder failures |
| Gateway packaging | Wheel built and installed in the new environment; isolated Python import resolved to its `site-packages` copy |
| Packaged browser assets | HTML, JavaScript, and CSS present and served successfully |
| Real loopback runtime | 25 C-origin frames accepted; zero parse, sequence, device-rejection, or subscriber-drop errors |
| Real WebSocket/renderer | 20 received events exercised the production JavaScript rendering handlers; missing barometer values and recovery verified |
| HTTP error paths | Missing device 404, invalid history limit 422, firmware-source fault control 409 |
| Dependency consistency | `pip check` passed |
| Static checks | Ruff passed for gateway, scripts, and integration tests; JavaScript syntax check passed |
| Protocol drift | Exported schema and compiled C fixture checks passed |
| Python coverage | **96.22% combined statement/branch coverage**; 1,071/1,097 statements and 279/306 branches covered |

The initial unmodified 249-test suite also passed. The new targeted startup
regression is what exposed the additional implementation error.

One non-failing upstream Starlette/httpx TestClient deprecation warning remains.
The live check used real HTTP and WebSocket transport, then exercised production
rendering functions in Node; it was not a graphical browser session. The positive
control's intentional C error is outside the project's compiled sources.

## Evidence

- [Final commands, outputs, and return codes](final-checks.json)
- [JUnit results](final-tests.xml)
- [Coverage details](coverage.json)
- [Fresh sanitized C build](build.json)
- [WebSocket regression before the fix](websocket-startup-before.txt)
- [WebSocket regressions after the fix](websocket-startup-after.xml)
- [Live installed-wheel HTTP/WebSocket check](live-runtime.json)
- [Seeded decoder checks](decoder-fuzz.json)
- [Sanitizer positive control](ubsan-control.json)
- [Verified source hashes](tested-source-sha256.json)
- [Isolation and comparison summary](summary.json)

From the retained sandbox, rerun the full host checks with:

```powershell
.venv/Scripts/python.exe scripts/verify.py --ctest C:/Users/sr116/Desktop/Resume/Projects/tmp/native-tools/Scripts/ctest.exe
.venv/Scripts/python.exe -m pytest tests/integration/test_websocket_startup.py -q
.venv/Scripts/python.exe -I evidence/decoder_fuzz.py
.venv/Scripts/python.exe -I evidence/runtime_check.py
```

STM32 hardware execution, physical power-loss/flash-journal behavior, real serial
devices, watchdog resets, Raspberry Pi deployment, and graphical browser behavior
were not validated in this run. Earlier target cross-build evidence remains in
the October 12 validation record; it is not a new hardware validation claim.
