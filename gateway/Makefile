.PHONY: install run test lint validate docker

install:
	python -m pip install -e ".[dev]"

run:
	sensor-monitor serve

test:
	pytest --cov=sensor_monitor --cov-report=term-missing

lint:
	ruff check .

validate:
	sensor-monitor validate examples/telemetry.jsonl

docker:
	docker build -t sensor-monitor .
