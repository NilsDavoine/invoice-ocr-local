#!/usr/bin/env bash
# Every measured number in results.md comes from this script.
#   ./run_benchmarks.sh            light engines only (CPU, a few minutes)
#   ./run_benchmarks.sh --vlm      + Xiaomi-OCR-0 on GPU (40 pages) and on CPU (3 pages)
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
LOCK=${MODEL_LOCK:-/tmp/invoice-ocr-model.lock}   # one heavy model at a time on a shared machine
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}
[ -f data/truth.json ] || $PY -m invoice_ocr generate
$PY -m invoice_ocr evaluate --engine textlayer
$PY -m invoice_ocr evaluate --engine rapidocr
if [ "${1:-}" = "--vlm" ]; then
  flock "$LOCK" $PY -m invoice_ocr evaluate --engine xiaomi --device cuda
  flock "$LOCK" $PY -m invoice_ocr evaluate --engine xiaomi --device cpu --limit 3
fi
$PY -m invoice_ocr stress
$PY -m tools.mutate
$PY -m invoice_ocr report
