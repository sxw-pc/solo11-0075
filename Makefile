PYTHON ?= python
PYTEST_ARGS ?= -q
export PYTHONPATH := $(CURDIR)/src

.DEFAULT_GOAL := test
.PHONY: test fixtures audit-sample

test: fixtures
	$(PYTHON) -m pytest $(PYTEST_ARGS)

fixtures:
	$(PYTHON) fixtures/build_fixtures.py

audit-sample: fixtures
	$(PYTHON) -m page_ledger audit --db fixtures/generated/clean.db --out out/sample
