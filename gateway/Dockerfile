FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SENSOR_MONITOR_HOST=0.0.0.0 \
    SENSOR_MONITOR_LOG_DIR=/var/log/sensor-monitor

RUN useradd --create-home --uid 10001 monitor
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .
RUN mkdir -p /var/log/sensor-monitor && chown -R monitor:monitor /var/log/sensor-monitor

USER monitor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["sensor-monitor", "serve"]
