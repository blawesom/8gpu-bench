# Tools

Phase 2 tooling for the 8gpu-bench specification.

## gen-corpus.py — corpus generator (spec 04)

Builds the payload profile corpora. One corpus per profile, per model
tokenizer. Both harnesses consume the same corpus file.

```
# 1. Fetch and pin the source records (one time).
python3 tools/gen-corpus.py fetch-sources --out-dir sources

# 2. Generate a corpus for a model tokenizer.
python3 tools/gen-corpus.py generate --profile PF-1 \
    --model Qwen/Qwen3.8-27B --sources-dir sources --out-dir corpora

# 3. Validate the generation logic without any dependency.
python3 tools/gen-corpus.py selftest
```

Output: `corpora/<model>/<profile>.jsonl` plus a `.meta.json` sidecar
with sources, revisions, attribution, expected cache hit, and the
corpus checksum. The first line of each corpus file is a `_meta`
record. Harness adapters skip it.

Sources (spec 04.2.2): PG-19 (session history), WildChat-1M (chat,
turn text), FineWeb (filler). Fetch records the dataset revisions.

## run-benchmark.py — test-point runner (spec 05.3)

Executes one run manifest: server lifecycle, health wait, warmup,
harness run, three repetitions. Writes `runs.jsonl` run records.

```
python3 tools/run-benchmark.py --config runs/sut1-vllm.yaml \
    --corpora corpora --out results/2026Q4 --harness guidellm
```

The manifest declares the SUT, the stack container, the model, and
the test points. See the docstring for the schema.

Adapter status: the GuideLLM adapter follows the documented CLI.
The GenAI-Perf adapter needs flag verification at the version pin
(spec 03.5). Raw harness output is always kept under `raw/`.

## build-results.py — results assembler (spec 07)

Merges `runs.jsonl` records into `results.csv` and `headlines.md`.

```
python3 tools/build-results.py --runs results/2026Q4 --out results/2026Q4
```
