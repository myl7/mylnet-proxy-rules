.PHONY: dev format format-check lint lint-mypy lint-ruff test

PY_FILES := $(shell find app tests -type f -name '*.py')
WEB_FILES := $(shell find app/static -type f)

dev:
	uvicorn app.main:app --reload --port 8000

format:
	ruff check --fix $(PY_FILES)
	ruff format $(PY_FILES)
	prettier --write $(WEB_FILES)

format-check:
	ruff format --check $(PY_FILES)
	prettier --check $(WEB_FILES)

lint: lint-ruff lint-mypy

lint-ruff:
	ruff check $(PY_FILES)

lint-mypy:
	mypy $(PY_FILES)

test:
	python3 -m unittest discover -s tests -t .
