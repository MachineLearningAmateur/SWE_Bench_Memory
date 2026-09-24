# Frozen graph-v2 retrieval policy

Status: frozen for the gpt-oss calibration and exploratory pilot. This is a retrieval-only development result, not evidence of agent benefit. No gpt-oss model call, calibration trial, or pilot trial informed this choice.

The development set is exactly `audit/retrieval_exposure/dev_task_ids.json` (30 tasks). It excludes every ID in the committed 12-task calibration and 24-task pilot partitions. The pinned benchmark ref is `ibragim-badertdinov/swe-rebench-07-2026@1`, task metadata SHA256 `e18fbd54e334d914ebe2981710ce0f5b9c3b9f169823560aced50a5443eb3a23`. The local read-only memory SQLite SHA256 is `3819f76349080c06ec0b09e246de40bbc1bf6efb19b875accf260f52f14a81c7`.

## Policy

`config/graph_v2.yaml` sets `graph_hops: 2` and `semantic_first: true` for the `gpt_oss_20b` profile. The same BM25 ranking and rendered seed prefix remain in Flat and Graph. Graph first considers reachable repair, contradiction, rationale, observed-effect, end-state, caveat, validation, and lesson relations in the existing deterministic order, then provenance/review relations. It follows at most eight neighbors and uses the same 24,000-character memory budget as Flat. The historical database is unchanged; Flat can search the same stored relation facts as text. Retrieval happens once at task start.

This is a prioritization change, not a claim that every retrieved relation is useful. Candidate lessons remain explicitly marked untrusted and provisional. Two-hop reachability may expose a relation whose intermediate node is represented by a seed review rather than rendered as a separate neighbor; this must be considered during trajectory audit.

## Development exposure

The prior one-hop policy reached neighbors on 24/30 tasks, but all followed edges were provenance/review scaffolding. A depth-only check showed substantive relations were reachable yet usually displaced by those edges. The candidate diagnostic found a non-scaffolding end-state/caveat neighbor available on 17/30 tasks at two hops. After semantic-first prioritization:

| Measure | Graph-v1 | Graph-v2 |
| --- | ---: | ---: |
| Any graph neighbor | 24/30 | 24/30 |
| Only provenance/review among activated | 24/24 | 5/24 |
| Repair/recovery relation | 0/30 | 1/30 |
| Contradiction/verification relation | 0/30 | 1/30 |
| Rationale/motivation relation | 0/30 | 4/30 |
| End-state/caveat relation | 0/30 | 17/30 |
| Raw source message inlined | 8/30 | 9/30 |

All 30 Flat/Graph common seed ID lists match; both contexts respect the 24,000-character budget; excluded-repository leakage is zero. Graph-v2 mean neighbor text is 9,548 characters and mean BM25 backfill is 5,635 characters. Repair and contradiction exposure remains sparse; the pilot can only provide an exploratory test of this frozen policy, with no guarantee of task relevance.

Full per-task output: `audit/gpt_oss_20b/graph_v2_dev_exposure.jsonl`; summary and exact policy settings/hash: `audit/gpt_oss_20b/graph_v2_dev_summary.json`. Reproduce with `python -m src.graph_v2_exposure_audit` against the unchanged memory DB. Do not rerun tuning with calibration or pilot task outcomes.
