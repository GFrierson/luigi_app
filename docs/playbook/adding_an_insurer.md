# Runbook: Adding an Insurer Profile

**Purpose:** Onboard a new insurer to EOB extraction. An insurer is one `IssuerProfile` plugged into the shared engine — nothing else changes.
**Use when:** an insurer is currently going through the LLM fallback often enough to be worth a deterministic profile.
**You produce:** one new `src/eob/profiles/<issuer>.py` + one `REGISTRY` line.

```python
# src/eob/profiles/<issuer>.py  — the ONLY thing you add
XXX_PROFILE = IssuerProfile(
    issuer="xxx",
    signatures={BlockType.HEADER: Signature(anchors=[...]),
                BlockType.DOC_BANNER: Signature(anchors=[...]),
                BlockType.CLAIM_BANNER: Signature(anchors=[...]),
                BlockType.CLAIM_TABLE: Signature(anchors=[...])},
    column_spec=ColumnSpec(anchors={"<header token>": "<column name>", ...},
                           row_terminator="Total",            # Cigna="Total", Anthem="Totals"
                           header_repeats_on_continuation=False),
    block_extractors={BlockType.CLAIM_TABLE:  TableBlockExtractor(column_spec),  # reuses parse_table
                      BlockType.CLAIM_BANNER: XxxClaimBanner(),
                      BlockType.HEADER:       XxxHeader(),
                      BlockType.DOC_BANNER:   XxxDocBanner()},
)
# src/eob/pipeline.py
REGISTRY["xxx"] = ProfileExtractor(XXX_PROFILE)
```

## Checklist
- [ ] Pull this insurer's samples from the `log_unknown` corpus — aim for coverage: ≥1 each of clean-TEXT vs IMAGE/garbage-text-layer, a multi-claim doc, a multi-page table, and every subtype you can find
- [ ] Profile the corpus: run `classify_pdf` over the batch — does this insurer ship TEXT, IMAGE, or a *garbage* text layer (like Anthem)? Note multi-doc artifacts (check/EOP). This decides whether the text layer is trustworthy
- [ ] **Check the claim-grouping model FIRST** (most common reason a profile won't fit): does each claim get its own banner+table like Anthem, or are multiple claims packed into one table under `PROVIDER, Claim #` subheaders like Cigna? If it matches the banner+table pairing, continue. If not, the profile needs its own claim-grouping/assembly strategy — stop and do a short design pass; **do not bend the shared engine**
- [ ] Add the issuer anchor to `identify(doc)` — the fixed string(s) that name this insurer (logo text, form code)
- [ ] Map front-page banners → `EOBSubtype` for the `doc_banner` extractor (extend the `EOBSubtype` literal only if a genuinely new subtype appears)
- [ ] Diff the claim-table columns against the header tokens → write the `ColumnSpec` (the diff *is* the spec): header-token→column map, `row_terminator`, `header_repeats_on_continuation`
- [ ] Write `signatures` per `BlockType` and the per-block extractors (`claim_banner`, `header`, `doc_banner`); `claim_table` is just `parse_table` + your `ColumnSpec` — reused, not rewritten
- [ ] If reason codes drive owe-interpretation, add this insurer's reason-code→meaning map (Anthem 015/ADU/033 ≠ Cigna A0/A1) — keep it in the profile
- [ ] Assemble `XXX_PROFILE` and register `REGISTRY["xxx"] = ProfileExtractor(XXX_PROFILE)`
- [ ] Mirror the Anthem test set against the new fixtures (classify kind, segment finds N claims, parse_table columns + multi-page stitch, subtype, claim counts, subscriber≠patient, validate reconciliation); commit the corpus samples to `tests/fixtures/`
- [ ] **Validation gate / cutover:** run the new profile across the whole corpus and diff its output against the LLM-extracted records already in `log_unknown`. Cut over from LLM-fallback to deterministic only when they match and `validate` reconciles. The corpus is your labeled regression set — that's why the LLM path logs it

## Cutover gate (eval harness)

Before promoting an insurer from the LLM fallback to its deterministic profile,
run the per-failure-mode eval harness and read its worst buckets — never a single
aggregate accuracy. The harness diffs each labeled fixture's extraction against
its `tests/fixtures/expected/<fixture>.json` expectation, one row per field, and
writes the `eval_results` table you group by.

```bash
python -m src.medical.eob.eval.cli \
  --fixture-dir tests/fixtures \
  --expected-dir tests/fixtures/expected \
  --eval-db /tmp/eval.db \
  --report worst
```

`worst_buckets()` returns the lowest-accuracy (insurer × kind × subtype × field)
combinations. Cut over only when every bucket for the new insurer clears the
threshold.

> **Threshold is a placeholder.** The current gate uses **0.90** match-rate per
> bucket, but this number is **not yet empirically calibrated** against the
> fixture corpus — it is a roadmap open blocker ("Confidence/score thresholds for
> the cutover gate set empirically against the fixture corpus"). Treat 0.90 as a
> starting point and recalibrate once the corpus is large enough to be
> representative; do not gate a production cutover on it blindly.

The `worst`, `by-insurer`, `by-column`, and `by-subtype` reports are all read off
the same `eval_results` table — pick the dimension that explains the failures
(e.g. `by-column` to see if one field is dragging the bucket down).

## Do NOT change
`segment`, `parse_table`, `ProfileExtractor`, `process_eob`, persistence, the Telegram harness. If a new insurer makes you want to, that's a signal it breaks a shared assumption (usually claim-grouping) — escalate to a design pass and add a profile-level strategy rather than editing the engine.

## Known gotchas
- **Garbage text layer:** verify per insurer; "has chars" ≠ "usable text". The quality gate handles it, but confirm the insurer's kind distribution.
- **Multi-doc packets:** EOB + EOP + check in one PDF, pages out of order — `detect_artifacts` flags these; they are out of profile scope in v1.
- **Claim grouping:** the banner+table pairing is Anthem-shaped. Cigna groups claims as in-table subheaders — that needs a profile-specific assembly strategy, not a `ColumnSpec` tweak.
- **Subtype vocabulary:** if the insurer has a document subtype not in `EOBSubtype`, decide whether to extend the shared literal or treat it as `summary`.
