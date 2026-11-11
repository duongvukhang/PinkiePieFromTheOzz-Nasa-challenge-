.PHONY: help setup run up down test test-py test-fe lint lint-py lint-fe format compose-check

PYTEST := $(if $(wildcard .venv/bin/pytest),.venv/bin/pytest,python3 -m pytest)
RUFF := $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,ruff)

help:
	@echo "Targets: setup | run | up | down | test | test-py | test-fe | lint | format | compose-check"

setup:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip setuptools
	.venv/bin/pip install -e ./science -e "./backend[dev]"
	npm --prefix frontend install

run:
	docker compose up --build

up:
	docker compose up -d

down:
	docker compose down

test: test-py test-fe

test-py:
	$(PYTEST)

test-fe:
	npm --prefix frontend test

lint: lint-py lint-fe

lint-py:
	$(RUFF) check backend science tests
	$(RUFF) format --check backend science tests

lint-fe:
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck
	npm --prefix frontend run format:check

format:
	$(RUFF) check --fix backend science tests
	$(RUFF) format backend science tests
	npm --prefix frontend run format

compose-check:
	docker compose config -q
