PY ?= .venv/bin/python
PYTHON ?= python3.12

setup:            ## light install (CPU)
	$(PYTHON) -m venv .venv && $(PY) -m pip install -r requirements.txt
setup-vlm:        ## + torch / transformers for the VLM engine
	$(PY) -m pip install -r requirements-vlm.txt
data:             ## 40 fictitious invoices + ground truth
	$(PY) -m invoice_ocr generate
test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PY) -m pytest -q
bench:            ## light engines, mutation test, results.md
	./run_benchmarks.sh
bench-vlm:        ## + Xiaomi-OCR-0 on GPU (40 pages) and CPU (3 pages)
	./run_benchmarks.sh --vlm
watch:            ## watched folder: watch/inbox -> watch/outbox | watch/rejects
	$(PY) -m invoice_ocr watch

.PHONY: setup setup-vlm data test bench bench-vlm watch
