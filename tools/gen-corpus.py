#!/usr/bin/env python3
"""8gpu-bench corpus generator. Implements spec 04.

Subcommands:
  fetch-sources  Pin and download source records to a local cache.
  generate        Build one profile corpus for one model tokenizer.
  selftest        Validate the generation logic with a mock tokenizer.

Source cache files (JSONL, one record per line):
  pg19.jsonl      {"text": str}                  session history text
  wildchat.jsonl  {"turns": [str, ...]}          chat turns, user first
  fineweb.jsonl   {"text": str}                  filler text

Corpus output (per profile, per model):
  <out>/<model>/<profile>.jsonl       requests, first line is a _meta record
  <out>/<model>/<profile>.meta.json   metadata sidecar with checksums

Usage examples:
  gen-corpus.py fetch-sources --out-dir sources
  gen-corpus.py generate --profile PF-1 --model Qwen/Qwen3.8-27B \\
      --sources-dir sources --out-dir corpora --seed 42
  gen-corpus.py selftest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
import time
from typing import Iterable

TOOL_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Profile registry (spec 04.2.1)
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    "PF-1": dict(kind="fixed", isl=512, osl=128, requests=2000,
                 source="fineweb", note="Short chat. Headline."),
    "PF-2": dict(kind="fixed", isl=8192, osl=1024, requests=2000,
                 source="fineweb", note="Long context. Headline."),
    "PF-3": dict(kind="chat", osl_cap=1024, requests=2000,
                 source="wildchat", note="Chat distribution. WildChat-1M."),
    "PF-4": dict(kind="dist", isl_mean=4096, isl_sd=2048, isl_min=512,
                 osl=256, requests=2000, source="fineweb",
                 note="Agentic / RAG shape."),
    "PF-5": dict(kind="fixed", isl=1024, osl=4096, requests=2000,
                 source="fineweb", note="Decode-heavy shape."),
    "PF-7": dict(kind="session", isl=30000, osl=500, sessions=60, turns=10,
                 source="pg19", note="Production p50."),
    "PF-8": dict(kind="session", isl=100000, osl=5000, sessions=25, turns=10,
                 source="pg19", note="Production p90."),
    "PF-9": dict(kind="session", isl=200000, osl=50000, sessions=10, turns=10,
                 source="pg19", note="Production p99."),
}

# Session model constants (spec 04.2.2 rules 9 and 10).
HISTORY_FRACTION_FIRST = 0.80
HISTORY_FRACTION_LAST = 0.97

SOURCE_DATASETS = {
    "pg19": "deepmind/pg19",
    "wildchat": "allenai/WildChat-1M",
    "fineweb": "HuggingFaceFW/fineweb",
}

ATTRIBUTION = {
    "pg19": ("Contains text from Project Gutenberg via PG-19 "
             "(deepmind/pg19). The books are public domain."),
    "wildchat": ("Contains data from WildChat-1M (allenai/WildChat-1M), "
                 "licensed under ODC-BY 1.0."),
    "fineweb": ("Contains data from FineWeb (HuggingFaceFW/fineweb), "
                "licensed under ODC-By 1.0."),
}

# ---------------------------------------------------------------------------
# Tokenizer handling
# ---------------------------------------------------------------------------


class MockTokenizer:
    """Whitespace tokenizer for selftest. No external dependencies."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()

    def decode(self, ids: list[str]) -> str:
        return " ".join(ids)


def load_tokenizer(model: str, mock: bool):
    if mock:
        return MockTokenizer()
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "transformers is required for real tokenizers. "
            "Install it, or use --mock for a selftest."
        ) from exc
    return AutoTokenizer.from_pretrained(model)


# ---------------------------------------------------------------------------
# Source cache
# ---------------------------------------------------------------------------


def read_jsonl(path: str) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TokenStream:
    """One lazily tokenized stream over a list of documents."""

    def __init__(self, texts: Iterable[str], tokenizer):
        self.texts = list(texts)
        self.tok = tokenizer
        self.doc_index = 0
        self.ids: list = []
        self.pos = 0
        self.tokens_taken = 0

    def take(self, n: int) -> list:
        """Return exactly n token ids. Tokens never repeat across calls."""
        if n <= 0:
            return []
        out: list = []
        while len(out) < n:
            if self.pos >= len(self.ids):
                if self.doc_index >= len(self.texts):
                    raise RuntimeError(
                        f"source exhausted after {self.tokens_taken} tokens; "
                        "fetch more source records"
                    )
                self.ids = self.tok.encode(
                    self.texts[self.doc_index], add_special_tokens=False
                )
                self.doc_index += 1
                self.pos = 0
                if not self.ids:
                    continue
            k = min(n - len(out), len(self.ids) - self.pos)
            out.extend(self.ids[self.pos : self.pos + k])
            self.pos += k
        self.tokens_taken += n
        return out


def fit_text(reserve: list, tokenizer, target: int) -> str:
    """Return text that encodes to exactly target tokens.

    reserve holds at least target token ids. Decode and re-encode are
    not always inverse operations. Adjust and retry until exact.
    """
    if len(reserve) < target:
        raise ValueError("reserve shorter than target")
    ids = reserve[:target]
    for _ in range(64):
        text = tokenizer.decode(ids)
        count = len(tokenizer.encode(text, add_special_tokens=False))
        if count == target:
            return text
        if count > target:
            trim = count - target
            if len(ids) - trim < 1:
                break
            ids = ids[: len(ids) - trim]
        else:
            need = target - count
            if len(ids) + need > len(reserve):
                break
            ids = ids + reserve[len(ids) : len(ids) + need]
    raise RuntimeError(f"could not fit exact token count {target}")


# ---------------------------------------------------------------------------
# Corpus builders
# ---------------------------------------------------------------------------


def build_fixed(spec: dict, stream: TokenStream, tokenizer, rng) -> tuple[list[dict], dict]:
    """PF-1, PF-2, PF-5: exact ISL from a unique token stream."""
    rows = []
    for i in range(spec["requests"]):
        isl = spec["isl"]
        reserve = stream.take(isl + 256)
        prompt = fit_text(reserve, tokenizer, isl)
        rows.append(
            dict(id=f"r{i:05d}", prompt=prompt,
                 max_tokens=spec["osl"], isl_target=isl)
        )
    return rows, {}


def build_distribution(spec: dict, stream: TokenStream, tokenizer, rng) -> tuple[list[dict], dict]:
    """PF-4: ISL sampled from a normal distribution, fixed seed."""
    rows = []
    for i in range(spec["requests"]):
        isl = max(spec["isl_min"], int(rng.gauss(spec["isl_mean"], spec["isl_sd"])))
        reserve = stream.take(isl + 256)
        prompt = fit_text(reserve, tokenizer, isl)
        rows.append(
            dict(id=f"r{i:05d}", prompt=prompt,
                 max_tokens=spec["osl"], isl_target=isl)
        )
    isls = [r["isl_target"] for r in rows]
    stats = dict(
        isl_mean=round(sum(isls) / len(isls), 1),
        isl_min=min(isls),
        isl_max=max(isls),
    )
    return rows, stats


def build_chat(spec: dict, records: list[dict], tokenizer) -> tuple[list[dict], dict]:
    """PF-3: real first user turns. ISL is empirical, OSL is capped."""
    rows = []
    for rec in records:
        turns = rec.get("turns") or []
        if not turns:
            continue
        prompt = turns[0].strip()
        if not prompt:
            continue
        isl = len(tokenizer.encode(prompt, add_special_tokens=False))
        if isl == 0:
            continue
        rows.append(
            dict(id=f"c{len(rows):05d}", prompt=prompt,
                 max_tokens=spec["osl_cap"], isl_target=isl)
        )
        if len(rows) >= spec["requests"]:
            break
    if len(rows) < spec["requests"]:
        raise RuntimeError(
            f"PF-3 needs {spec['requests']} non-empty conversations, "
            f"got {len(rows)}. Fetch more WildChat records."
        )
    isls = sorted(r["isl_target"] for r in rows)
    stats = dict(
        isl_mean=round(sum(isls) / len(isls), 1),
        isl_median=isls[len(isls) // 2],
        isl_p90=isls[int(len(isls) * 0.9)],
        osl_cap=spec["osl_cap"],
    )
    return rows, stats


def build_sessions(spec: dict, book_stream: TokenStream, chat_stream: TokenStream,
                   tokenizer, rng) -> tuple[list[dict], dict]:
    """PF-7 to PF-9: session model per spec 04.2.2 rules 8 to 11.

    Turn t prompt = history (book tokens, growing) + new turn text
    (chat tokens). The history fraction grows from 0.80 to 0.97
    across T turns. The expected token-level cache hit follows.
    """
    turns = spec["turns"]
    isl = spec["isl"]
    osl = spec["osl"]
    fractions = [
        HISTORY_FRACTION_FIRST
        + (HISTORY_FRACTION_LAST - HISTORY_FRACTION_FIRST) * t / (turns - 1)
        for t in range(turns)
    ]
    rows = []
    hit_tokens = 0
    for s in range(spec["sessions"]):
        book_reserve = book_stream.take(int(isl * 1.02) + 256)
        for t in range(turns):
            history = int(isl * fractions[t])
            new_len = isl - history
            turn_ids = chat_stream.take(new_len)
            combined = book_reserve[:history] + turn_ids
            prompt = fit_text(combined, tokenizer, isl)
            rows.append(
                dict(id=f"s{s:03d}t{t + 1:02d}", session_id=s, turn=t + 1,
                     prompt=prompt, max_tokens=osl, isl_target=isl)
            )
            if t > 0:
                hit_tokens += int(isl * fractions[t - 1])
    expected_hit = round(hit_tokens / (len(rows) * isl), 4)
    stats = dict(
        sessions=spec["sessions"], turns_per_session=turns,
        expected_cache_hit=expected_hit,
        history_fraction_first=HISTORY_FRACTION_FIRST,
        history_fraction_last=HISTORY_FRACTION_LAST,
    )
    return rows, stats


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_corpus(out_dir: str, profile: str, rows: list[dict], meta: dict) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    corpus_path = os.path.join(out_dir, f"{profile}.jsonl")
    meta_path = os.path.join(out_dir, f"{profile}.meta.json")

    meta_record = dict(_meta=True, **{k: v for k, v in meta.items() if k != "corpus_sha256"})
    with open(corpus_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta_record, ensure_ascii=False) + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    corpus_sha = sha256_file(corpus_path)
    meta["corpus_sha256"] = corpus_sha
    meta["n_requests"] = len(rows)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return corpus_path, meta_path


def cmd_generate(args) -> int:
    if args.profile not in PROFILES:
        raise SystemExit(f"unknown profile {args.profile}")
    spec = PROFILES[args.profile]
    tokenizer = load_tokenizer(args.model, args.mock)
    rng = random.Random(args.seed)

    pg19 = read_jsonl(os.path.join(args.sources_dir, "pg19.jsonl"))
    wildchat = read_jsonl(os.path.join(args.sources_dir, "wildchat.jsonl"))
    fineweb = read_jsonl(os.path.join(args.sources_dir, "fineweb.jsonl"))

    source_meta = []
    for name, fname in (("pg19", "pg19.jsonl"), ("wildchat", "wildchat.jsonl"),
                        ("fineweb", "fineweb.jsonl")):
        path = os.path.join(args.sources_dir, fname)
        source_meta.append(
            dict(name=name, dataset=SOURCE_DATASETS[name], file=fname,
                 sha256=sha256_file(path), records=len(read_jsonl(path)))
        )

    if spec["kind"] == "chat":
        rows, stats = build_chat(spec, wildchat, tokenizer)
    elif spec["kind"] == "session":
        book_stream = TokenStream((r["text"] for r in pg19), tokenizer)
        turn_texts = []
        for rec in wildchat:
            turn_texts.extend(rec.get("turns") or [])
        if not turn_texts:
            raise SystemExit("wildchat.jsonl has no turns")
        chat_stream = TokenStream(turn_texts, tokenizer)
        rows, stats = build_sessions(spec, book_stream, chat_stream, tokenizer, rng)
    else:
        stream = TokenStream((r["text"] for r in fineweb), tokenizer)
        if spec["kind"] == "dist":
            rows, stats = build_distribution(spec, stream, tokenizer, rng)
        else:
            rows, stats = build_fixed(spec, stream, tokenizer, rng)

    used_sources = sorted({spec["source"], "wildchat"} if spec["kind"] == "session"
                          else {spec["source"]})
    model_dir = args.model.replace("/", "_")
    out_dir = os.path.join(args.out_dir, model_dir)
    meta = dict(
        tool_version=TOOL_VERSION,
        profile=args.profile,
        kind=spec["kind"],
        note=spec.get("note", ""),
        model=args.model,
        seed=args.seed,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        isl_target=spec.get("isl"),
        osl_target=spec.get("osl", spec.get("osl_cap")),
        stats=stats,
        sources=[s for s in source_meta if s["name"] in used_sources],
        attribution=[ATTRIBUTION[name] for name in used_sources],
    )
    corpus_path, meta_path = write_corpus(out_dir, args.profile, rows, meta)
    print(f"wrote {corpus_path} ({len(rows)} requests)")
    print(f"wrote {meta_path}")
    if "expected_cache_hit" in stats:
        print(f"expected cache hit: {stats['expected_cache_hit']:.1%}")
    return 0


# ---------------------------------------------------------------------------
# Source fetch
# ---------------------------------------------------------------------------


def cmd_fetch(args) -> int:
    try:
        from datasets import load_dataset
        from huggingface_hub import HfApi
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pip install datasets huggingface_hub") from exc

    api = HfApi()
    os.makedirs(args.out_dir, exist_ok=True)

    def resolved_revision(dataset: str) -> str:
        try:
            info = api.dataset_info(dataset)
            return info.sha or "unknown"
        except Exception:
            return "unknown"

    # PG-19: long books for session history.
    rev = resolved_revision(SOURCE_DATASETS["pg19"])
    out = os.path.join(args.out_dir, "pg19.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        ds = load_dataset(SOURCE_DATASETS["pg19"], split="train",
                          streaming=True, revision=args.revision)
        count = 0
        for rec in ds:
            text = rec.get("text") or ""
            if len(text.split()) < 500:  # skip very short texts
                continue
            fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
            count += 1
            if count >= args.pg19_books:
                break
    print(f"pg19: {count} books, revision {rev}")

    # WildChat-1M: conversations. Store user-facing turns only.
    rev = resolved_revision(SOURCE_DATASETS["wildchat"])
    out = os.path.join(args.out_dir, "wildchat.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        ds = load_dataset(SOURCE_DATASETS["wildchat"], split="train",
                          streaming=True, revision=args.revision)
        count = 0
        for rec in ds:
            turns = []
            for msg in rec.get("conversation") or []:
                content = (msg.get("content") or "").strip()
                if content:
                    turns.append(content)
            if not turns:
                continue
            fh.write(json.dumps({"turns": turns}, ensure_ascii=False) + "\n")
            count += 1
            if count >= args.wildchat_convs:
                break
    print(f"wildchat: {count} conversations, revision {rev}")

    # FineWeb (sample-10BT config): filler text.
    rev = resolved_revision(SOURCE_DATASETS["fineweb"])
    out = os.path.join(args.out_dir, "fineweb.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        ds = load_dataset(SOURCE_DATASETS["fineweb"], "sample-10BT",
                          split="train", streaming=True, revision=args.revision)
        count = 0
        tokens = 0
        for rec in ds:
            text = rec.get("text") or ""
            words = len(text.split())
            if words < 100:  # keep docs that can feed PF-1 and larger
                continue
            fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
            count += 1
            tokens += words
            if count >= args.fineweb_docs or tokens >= args.fineweb_max_tokens:
                break
    print(f"fineweb: {count} docs, about {tokens} whitespace tokens, revision {rev}")
    return 0


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


def _common_prefix_len(a: list, b: list) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def cmd_selftest(args) -> int:
    tok = MockTokenizer()
    rng = random.Random(42)

    # Fabricated sources. Words are unique so prompts never repeat.
    fineweb = [{"text": " ".join(f"fw{i}_{j}" for j in range(400))}
               for i in range(200)]
    pg19 = [{"text": " ".join(f"bk{i}_{j}" for j in range(60000))}
            for i in range(8)]
    wildchat = [{"turns": [f"turn{i}_{j}" for j in range(60)]}
                for i in range(400)]

    def scaled(profile: str, spec: dict) -> dict:
        spec = dict(spec)
        if spec["kind"] == "session":
            spec["isl"] = 300
            spec["sessions"] = 3
        elif spec["kind"] == "dist":
            spec["isl_mean"] = 50
            spec["isl_sd"] = 20
            spec["isl_min"] = 10
            spec["requests"] = 20
        else:
            spec["isl"] = 50
            spec["requests"] = 20
        return spec

    failures = []
    for profile, base_spec in PROFILES.items():
        spec = scaled(profile, base_spec)
        if spec["kind"] == "chat":
            rows, stats = build_chat(spec, wildchat, tok)
        elif spec["kind"] == "session":
            book = TokenStream((r["text"] for r in pg19), tok)
            turn_texts = [t for rec in wildchat for t in rec["turns"]]
            chat = TokenStream(turn_texts, tok)
            rows, stats = build_sessions(spec, book, chat, tok, rng)
        elif spec["kind"] == "dist":
            stream = TokenStream((r["text"] for r in fineweb), tok)
            rows, stats = build_distribution(spec, stream, tok, rng)
        else:
            stream = TokenStream((r["text"] for r in fineweb), tok)
            rows, stats = build_fixed(spec, stream, tok, rng)

        # Check 1: exact ISL on controlled profiles.
        if spec["kind"] != "chat":
            for r in rows:
                got = len(tok.encode(r["prompt"], add_special_tokens=False))
                if got != r["isl_target"]:
                    failures.append(f"{profile} {r['id']}: ISL {got} != {r['isl_target']}")
                    break

        # Check 2: unique prompts.
        prompts = [r["prompt"] for r in rows]
        if len(set(prompts)) != len(prompts):
            failures.append(f"{profile}: prompts repeat")

        # Check 3: session prefix property and expected hit.
        if spec["kind"] == "session":
            by_session: dict[int, list[dict]] = {}
            for r in rows:
                by_session.setdefault(r["session_id"], []).append(r)
            for sid, srows in by_session.items():
                srows.sort(key=lambda r: r["turn"])
                for a, b in zip(srows, srows[1:]):
                    ids_a = tok.encode(a["prompt"], add_special_tokens=False)
                    ids_b = tok.encode(b["prompt"], add_special_tokens=False)
                    shared = _common_prefix_len(ids_a, ids_b)
                    expect = int(spec["isl"] * HISTORY_FRACTION_FIRST) + \
                        int((int(spec["isl"] * HISTORY_FRACTION_LAST) -
                             int(spec["isl"] * HISTORY_FRACTION_FIRST))
                            * (a["turn"] - 1) / (spec["turns"] - 1))
                    if abs(shared - expect) > 2:
                        failures.append(
                            f"{profile} s{sid} turn {a['turn']}: shared {shared}, "
                            f"expected about {expect}")
                        break
            hit = stats["expected_cache_hit"]
            if not 0.75 <= hit <= 0.85:
                failures.append(f"{profile}: expected hit {hit} outside 0.75 to 0.85")

        # Check 4: OSL positive.
        for r in rows:
            if r["max_tokens"] <= 0:
                failures.append(f"{profile} {r['id']}: bad max_tokens")
                break

        # Check 5: write and read back.
        with tempfile.TemporaryDirectory() as tmp:
            meta = dict(tool_version=TOOL_VERSION, profile=profile,
                        kind=spec["kind"], model="mock", seed=42,
                        stats=stats, sources=[], attribution=[])
            cpath, mpath = write_corpus(tmp, profile, rows, meta)
            if not os.path.exists(cpath) or not os.path.exists(mpath):
                failures.append(f"{profile}: output files missing")
                continue
            back = read_jsonl(cpath)
            if len(back) != len(rows) + 1:  # +1 for the _meta header line
                failures.append(f"{profile}: readback count mismatch")
            if json.load(open(mpath))["corpus_sha256"] != sha256_file(cpath):
                failures.append(f"{profile}: checksum mismatch")

    if failures:
        print("SELFTEST FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"SELFTEST PASSED: all {len(PROFILES)} profiles valid")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch-sources", help="pin and download source records")
    p.add_argument("--out-dir", default="sources")
    p.add_argument("--revision", default="main",
                   help="dataset git revision (default: main)")
    p.add_argument("--pg19-books", type=int, default=300)
    p.add_argument("--wildchat-convs", type=int, default=25000)
    p.add_argument("--fineweb-docs", type=int, default=100000)
    p.add_argument("--fineweb-max-tokens", type=int, default=50_000_000,
                   help="whitespace-token budget for the filler cache")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("generate", help="build one profile corpus")
    p.add_argument("--profile", required=True, choices=sorted(PROFILES))
    p.add_argument("--model", default="mock",
                   help="HF model id or path for the tokenizer")
    p.add_argument("--mock", action="store_true",
                   help="use the whitespace mock tokenizer (selftest only)")
    p.add_argument("--sources-dir", default="sources")
    p.add_argument("--out-dir", default="corpora")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("selftest", help="validate the logic with a mock tokenizer")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
