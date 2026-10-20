# Deploy the combined gateway

Install the checkout at `/opt/stm32-networked-sensor-system`, create `.venv`
there, and install `./gateway`. Create the `sensor-monitor` service user with
serial access and a writable `/var/log/sensor-monitor`. Put port settings in
`/etc/sensor-system.env` and install `sensor-system.service` with systemd.

The unit defaults to serial input. Set `SENSOR_MONITOR_SERIAL_PORT` to the
board's stable device path. Gateway configuration is listed in
[`gateway/.env.example`](../gateway/.env.example). Binding beyond loopback
requires explicitly setting `SENSOR_MONITOR_HOST`; the API is unauthenticated.

The existing container recipe is built from its component context:
`docker build -t stm32-sensor-gateway gateway`. Serial passthrough, device
permissions, systemd installation, and Pi operation require target validation;
they were not installed or exercised during this host integration.
