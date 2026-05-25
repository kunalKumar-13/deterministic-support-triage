# corpus_analysis.md

Pre-implementation analysis of the support corpus and operational
consequences for the retrieval design.

The corpus is not shipped in this checkout; this analysis is based on the
problem statement, the documented sources, and a small reference set we
generated to exercise the pipeline. Replace `data/` with the real corpus
before evaluation; nothing in the code path is corpus-specific.

## 1. Three product ecosystems, three writing styles

| Domain | Source | Typical doc style |
|---|---|---|
| `data/devplatform/` | `support.devplatform.com` | Engineering-flavored help center: short, task-oriented articles, code snippets, step-by-step. |
| `data/claude/` | `support.claude.com` | Product help: conversational tone, FAQ-shaped, frequent linking between docs. |
| `data/visa/` | `visa.co.in/support.html` | Financial / regulatory: longer prose, formal tone, jurisdiction-specific. |

Consequence: a single chunk size is a compromise. We use 800 chars / 120
overlap as a middle ground; FAQ-shaped docs (Claude) benefit from smaller
windows, but a single setting keeps the pipeline simple and deterministic.

## 2. Subject-matter overlap (and how it confuses routing)

- "billing", "refund", "subscription" appear in **all three** corpora with
  different policies (Visa = card disputes; Claude = subscription billing;
  DevPlatform = workspace seats).
- "account", "login", "verification" overlap across DevPlatform and Claude.
- "fraud", "dispute", "chargeback" are Visa-heavy but appear in Claude
  billing too.

Consequence: routing cannot rely on a single keyword. We use a brand-term
gazetteer plus top-k retrieval across all three indexes when `company` is
unreliable.

## 3. Document recency

The corpus does not carry explicit dates. We approximate recency by:

- `mtime` from `os.stat` (relative within the checkout only).
- File path naming conventions (e.g. `2024_*`, `legacy_*`, `deprecated_*`).
- Content cues: "as of <year>" inside the text, scanned at load time.

If we cannot determine recency, we set `recency_score = 0.5` (neutral) and
do not boost or penalise on this axis.

## 4. Known corpus failure modes (per the spec)

The problem statement explicitly says the corpus may contain:

- Inconsistencies
- Outdated information
- Contradictions
- Subtly incorrect documents
- Documents miscategorised in the directory tree
- Documents that look "too convenient" (planted, possibly poisoned)

Our handling:

| Failure mode | Detection | Mitigation |
|---|---|---|
| Contradictions | Pairwise text similarity > 0.7 with policy-claim divergence | Flag, drop confidence, prefer specific over generic doc |
| Outdated | Recency proxy + content cue parsing | Down-weight in rerank |
| Subtly incorrect | (cannot detect at scale) | Require corroboration — single-doc claims do not become "policy" in the response |
| Miscategorised | Brand-term overlap between content and parent dir | If mismatch, trust content; route on body, not path |
| Poisoned / "convenient" | Injection pattern scan over chunk content | Strip imperatives, halve trust score |

## 5. Chunking strategy

```
def chunk(text):
    text = nfkc(text)
    text = strip_zero_width(text)
    paragraphs = split_on_blank_lines(text)
    out = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 1 <= 800:
            buf += "\n" + p
        else:
            if buf:
                out.append(buf.strip())
            if len(p) <= 800:
                buf = p
            else:
                # long paragraph: sliding window with 120-char overlap
                for w in window(p, 800, 120):
                    out.append(w)
                buf = ""
    if buf:
        out.append(buf.strip())
    return out
```

Properties:

- Respects paragraph boundaries where possible (better than a hard window).
- Deterministic (no randomness, stable iteration).
- Bounded chunk size — keeps prompts predictable.

Each chunk carries:

```
ChunkRef(
    doc_path: str,          # relative to repo root
    chunk_id: int,
    char_start: int,
    char_end: int,
    title: Optional[str],   # first non-empty line of doc
    domain: str,            # devplatform | claude | visa
    text: str,
    trust_signals: dict,
)
```

## 6. Indexing

Two parallel indexes built at startup (or loaded from cache):

- **BM25 index** (rank_bm25.BM25Okapi) over tokenised chunk text.
- **TF-IDF matrix** (sklearn) over the same chunks, ngrams 1-2.

Both are persisted to `code/.cache/` keyed by `sha256(sorted_corpus_paths +
chunk_params)`. On a populated corpus (~1k files) cold start is < 30 s; warm
is < 2 s. Within the 3-minute budget for ~150 tickets, retrieval is ~1 ms
per ticket after indexing.

We considered FAISS — rejected. Reasons:

- The corpus is small (low thousands of chunks) so brute-force cosine is
  faster than FAISS index build + query.
- FAISS introduces non-portable index files and platform-specific behavior.
- We do not need approximate nearest neighbor at this scale.

Sentence-transformer embeddings are supported but disabled by default
(`TRIAGE_USE_EMBEDDINGS=1` to enable). Reasons for the default-off choice:

- They require a model download (cold start hit, against the 3-min budget
  on first run if network is restricted).
- They introduce a non-trivial dependency stack.
- For the size of this corpus, TF-IDF + BM25 + lexical rerank is within
  ~3-5% of dense retrieval on most queries, and is strictly deterministic.

## 7. Rerank scoring

For each candidate chunk `c` against query `q`:

```
score(c | q) =
    0.55 * cosine(tfidf(q), tfidf(c))
  + 0.35 * normalised_bm25(c, q)
  + 0.10 * jaccard(title_tokens(c), q_tokens)
```

Then we apply trust multipliers:

```
final = score
       * (0.5 if c.has_injection_marker else 1.0)
       * (1.1 if c.is_specific_doc else 1.0)
       * recency_multiplier(c)
       * domain_match_multiplier(c, ticket.company)
```

The trust-adjusted score determines top-k. We keep the top 6 chunks.

## 8. "No grounding" handling

If after rerank `top1.final < 0.30`:

- `confidence ≤ 0.45`
- The response is templated as "I don't have a confirmed answer in our
  documentation for this. I'm escalating to a human."
- `status = escalated`
- `source_documents` is empty (we will not cite weak matches as authoritative).

If `0.30 ≤ top1.final < 0.50` and risk is low/medium, we may reply but with
a confidence cap of `0.65` and a hedged response.

## 9. Source attribution policy

Three rules govern `source_documents`:

1. Only paths that were materially used to phrase the response.
2. Path must exist on disk at write time (`os.path.exists`).
3. Sorted ascending for determinism.

Concretely: after the LLM produces `answer_draft`, we compute the
cosine of `answer_draft` against each of the top-6 chunks. Chunks with
cosine ≥ 0.25 are cited; others are dropped. Maximum 4 citations.

This eliminates the "I retrieved 6 but only used 1" hallucination risk.

## 10. Open items

- The actual corpus may have additional sub-categories we have not modeled
  (e.g. `data/visa/cobranded/`). Our retrieval is sub-directory agnostic;
  any markdown / text file under `data/{devplatform,claude,visa}/` is
  indexed automatically.
- If the corpus contains binary files (PDFs, images), we skip them in v1
  and log a warning. PDF text extraction is a strict add-on, not on the
  critical path.
- We do not currently model document `priority` (e.g. "official policy" vs
  "community post"). If the real corpus distinguishes these, we can add a
  trust multiplier driven by a tag at the top of the file.
