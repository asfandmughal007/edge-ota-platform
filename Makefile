.PHONY: install lint test check up down demo logs clean

PY ?= python
VENV ?= .venv
BIN := $(VENV)/bin
ifeq ($(OS),Windows_NT)
BIN := $(VENV)/Scripts
endif

install:
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install -e "agent[dev]" -e "backend[dev]"

lint:
	cd agent && ../$(BIN)/python -m ruff check . && ../$(BIN)/python -m ruff format --check . && ../$(BIN)/python -m mypy
	cd backend && ../$(BIN)/python -m ruff check . && ../$(BIN)/python -m ruff format --check . && ../$(BIN)/python -m mypy

test:
	cd agent && ../$(BIN)/python -m pytest -q
	cd backend && ../$(BIN)/python -m pytest -q

check: lint test

up:
	docker compose up -d --build

down:
	docker compose down

demo:
	$(BIN)/python scripts/demo.py

logs:
	docker compose logs -f backend simulator-001 simulator-002 simulator-003

# Also removes volumes (database, registry, tokens) and the app containers the agents started.
clean:
	docker compose down -v
	-docker rm -f edge-app-device-001 edge-app-device-002 edge-app-device-003
