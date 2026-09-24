# Graph traversal depth check on retrieval-development tasks

This is a retrieval-only development diagnostic on the 30 task IDs in `audit/retrieval_exposure/dev_task_ids.json`. It made zero model calls and used no calibration or pilot task. The exact per-task relation exposures are in `audit/gpt_oss_20b/graph_dev_hop_comparison.json`. The script is `python -m src.graph_v2_dev_audit`.

The only varied parameter was `graph_hops` (1, 2, or 3). All runs retained the pinned memory database, the same BM25 seed policy, 24,000-character budget, eight-neighbor bound, and allowed relations. Relation categories are: A provenance/review; B repair/recovery; C contradiction/verification; D observed effects; E rationale/motivation; F end-state/caveat.

| Hops | Tasks with any neighbor | A | B | C | D | E | F |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 24 | 24 | 0 | 0 | 0 | 0 | 0 |
| 2 | 24 | 24 | 1 | 1 | 1 | 2 | 0 |
| 3 | 24 | 24 | 2 | 2 | 1 | 0 | 0 |

Depth alone does not make the graph arm reliably expose the intended relations. No graph-v2 policy is frozen by this check. The 24-task, three-arm pilot remains gated pending a development-only policy with useful exposure and a committed freeze artifact.
