# Memory package

The current Failure50 experiment uses `failure50_transfer.zip`, restored with
`python -m src.restore_transfer`. The optional `historical_experiments.zip` is
restored with `--history`. Both are verified against
`audit/failure50_v1/transfer_manifest.json`. See
[the machine handoff](../docs/machine_handoff.md) for setup and execution.

These transfer packages are separate from the original legacy ZIP below.

The **FULL** download includes `trajdebug_flat_rag_86_COMPLETE.zip` in this folder. The CODE_ONLY download does not; copy your earlier `trajdebug_flat_rag_86_COMPLETE.zip` here before running the notebooks.

Why only the flat ZIP? It is the **same-information corpus**: it losslessly preserves the full base graph,
semantic overlay, reviews, evidence, and relationship records. The experiment uses the *same SQLite corpus*
for both memory arms:

- `flat_rag`: BM25-ranked chunks only; never follows links.
- `graph`: identical BM25 seeds, then interprets the preserved relationship records as edges and performs a bounded traversal.

That makes the experimental difference much cleaner than pointing the two arms at unrelated readers.
