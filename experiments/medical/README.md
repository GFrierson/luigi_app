# Medical Document Experiments

Annotation CSVs and eval infrastructure for the deterministic extractor playbook.

## Directory layout

experiments/medical/{insurer}_{doc_type}/
    sample/          ← raw PDF samples (gitignored, never committed)
    annotations.csv  ← ground-truth annotation CSV (committed)

## Annotation CSV format

Each row represents one document. Columns:

| Column | Description |
|--------|-------------|
| file_path | Relative path from this directory to the PDF |
| _true_{field} | Ground-truth value for {field}, filled manually |
| _hyp_{field} | Hypothesis value pre-filled by the annotation script |
| _review_status | "verified" once manually confirmed; "pending" otherwise |

Only rows with _review_status=verified are used by eval scripts.
