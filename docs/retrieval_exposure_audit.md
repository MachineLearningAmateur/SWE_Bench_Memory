# Retrieval Exposure Audit

Retrieval-only diagnostic. **No model calls, no agent runs, no task solving, no grading.**
This audit measures what historical memory each frozen system would *expose* for a new issue;
it cannot establish that graph memory improves task outcomes.

MODEL CALLS MADE: 0

## Benchmark source

- Dataset: `ibragim-badertdinov/swe-rebench-07-2026` (ref `1`)
- URL: https://hub.harborframework.com/datasets/ibragim-badertdinov/swe-rebench-07-2026/latest
- Derived from: `nebius/SWE-rebench-leaderboard`
- Listing task count: 111
- Task-metadata SHA256: `e18fbd54e334d914ebe2981710ce0f5b9c3b9f169823560aced50a5443eb3a23`
- Languages: {'go': 21, 'java': 21, 'python': 20, 'rust': 25, 'typescript': 24}

## Task partition

- Original task count: 111
- Excluded (memory repositories): 0 []
- Eligible task count: 111
- Audited: 30

## Selected task IDs

```
sipeed__picoclaw-2928
ludo-technologies__pyscn-548
astral-sh__ruff-25414
apache__pulsar-25953
ArcadeData__arcadedb-4411
Soju06__codex-lb-744
raullenchai__Rapid-MLX-426
marimo-team__marimo-9754
vuejs__core-14877
microsoft__waza-247
rest-sh__restish-337
rust-lang__rust-analyzer-22397
ubugeeei-prod__vize-769
floci-io__floci-1325
livekit__agents-5944
marimo-team__marimo-9766
kestra-io__kestra-16067
microsoft__typescript-go-4194
kubernetes-sigs__kueue-11559
woodpecker-ci__woodpecker-6623
openrewrite__rewrite-7784
floci-io__floci-1235
gotd__td-1759
facebook__lexical-8676
mui__base-ui-4903
ivov__lisette-657
nearai__ironclaw-3694
fallow-rs__fallow-913
fathah__hermes-desktop-268
PerryTS__perry-3982
```

## Exposure statistics

- Graph traversal activated: 80.0%
- Graph found >=1 neighbor: 80.0%
- Only provenance/scaffolding relations (of activated): 100.0%
- At least one recovery/repair relation: 0.0%
- Contradiction/verification relation: 0.0%
- Rationale relation: 0.0%
- End-state/caveat relation: 0.0%
- Inlined raw source evidence: 26.67%
- Fell back entirely to BM25 after common seeds: 20.0%
- Avg graph-specific neighbor chars: 5021.04
- Avg BM25 backfill chars: 8925.53
- Avg flat context chars: 23995.2
- Avg graph context chars: 23987.5
- Avg graph neighbors: 2.1
- Excluded-repo leakage incidents: 0
- Common seeds identical (all tasks): True
- Character budget respected (all tasks): True

## Automated observations

- Graph traversal activated on 24/30 audited tasks.
- Graph inlined raw cited source evidence on 8/30 audited tasks.
- Graph fell back entirely to BM25 backfill on 6/30 audited tasks.
- Relations followed were confined to categories: A (provenance_review_scaffolding).
- These are exposure observations only; they do not establish that graph memory improves task outcomes.

## Relation frequency

| Relation | Count | Category |
| --- | ---: | --- |
| CITES_SOURCE_MESSAGE | 53 | A |
| ASSESSES_AGAINST | 10 | A |

## Relation category frequency

| Category | Meaning | Count |
| --- | --- | ---: |
| A | provenance_review_scaffolding | 63 |

## Strongest graph activation

```
gotd__td-1759
ivov__lisette-657
ludo-technologies__pyscn-548
fathah__hermes-desktop-268
microsoft__waza-247
fallow-rs__fallow-913
marimo-team__marimo-9754
woodpecker-ci__woodpecker-6623
kestra-io__kestra-16067
openrewrite__rewrite-7784
```

## Zero graph activation

```
astral-sh__ruff-25414
facebook__lexical-8676
floci-io__floci-1325
microsoft__typescript-go-4194
rust-lang__rust-analyzer-22397
vuejs__core-14877
```

## Interpretation guardrails

Valid: activation rates, relation-category rates, raw-evidence exposure, BM25-only fallback,
flat/graph differences, and whether humans judge extra context as potentially relevant.

Invalid (needs the later agent experiment): "graph helps agents", "graph improves accuracy",
"graph memory is superior to RAG".

