# July 2026 SWE-rebench: gpt-oss-20b calibration

Completed 2026-09-28. This is the frozen, no-memory calibration, **not** a
graph-versus-flat-RAG-versus-no-memory comparison. Source:
`ibragim-badertdinov/swe-rebench-07-2026@1`; twelve prespecified tasks,
four each in Python, TypeScript, and Go. The task IDs and repo grouping were
frozen before these outcomes in `audit/gpt_oss_20b/calibration_task_ids.json`.
The agent was mini-SWE-agent 2.4.6 with Azure Foundry gpt-oss-20b,
temperature 0, one attempt per task, and a 100-step limit. The narrowly
specified malformed-tool-call repair was active; no text-only submission
fallback, reminder, retry, or task extension was used.

## Decision

**0/12 resolved under the predeclared submit-required rule.** The calibration
gate is `floor_stop` (0–2/12), so the 24-task × 3-condition pilot was **not
run**. This is a likely floor/protocol-completion limitation for this agent
configuration, not evidence that graph, flat RAG, or no memory is better.

Harbor's raw verifier awarded **1/12** working trees. That one task,
`intellectronica__ruler-556`, exited with `RepeatedFormatError` after making
changes, never issued the submit command, and had no submission patch.
Because Harbor's verifier tests the shared working tree, it could award 1
despite the missing agent submission. We preserve that official reward in
the audit but count the task unresolved under the rule frozen before the
calibration. Thus `official_reward_sum=1` and `resolved=0` intentionally
measure different things. Either number is below the pilot's 3/12 floor.

## Trial outcomes

| Task | Language | Agent exit | Raw reward | Submit-required resolved | Model calls |
|---|---|---|---:|---:|---:|
| `open-jarvis__OpenJarvis-465` | Python | RepeatedFormatError | 0 | 0 | 27 |
| `harbor-framework__harbor-1764` | Python | LimitsExceeded | 0 | 0 | 100 |
| `agno-agi__agno-8148` | Python | LimitsExceeded | 0 | 0 | 100 |
| `mozilla-ai__any-llm-1121` | Python | Submitted | 0 | 0 | 52 |
| `tashfeenahmed__freellmapi-289` | TypeScript | Submitted | 0 | 0 | 29 |
| `fathah__hermes-desktop-249` | TypeScript | RepeatedFormatError | 0 | 0 | 36 |
| `mikro-orm__mikro-orm-7799` | TypeScript | RepeatedFormatError | 0 | 0 | 85 |
| `intellectronica__ruler-556` | TypeScript | RepeatedFormatError | 1 | 0 | 41 |
| `ludo-technologies__pyscn-500` | Go | Submitted | 0 | 0 | 38 |
| `kenn-io__agentsview-602` | Go | RepeatedFormatError | 0 | 0 | 26 |
| `cloudnative-pg__cloudnative-pg-10747` | Go | LimitsExceeded | 0 | 0 | 100 |
| `docker__docker-agent-2992` | Go | RepeatedFormatError | 0 | 0 | 84 |

Six trials exited after repeated tool-format errors; three exhausted the
100-step limit; three issued the explicit submit marker. None had a non-empty
submission patch, and none met the submit-required resolution criterion.
The trajectories contain 36 recorded format-error feedback messages across
718 model calls. Total reported usage was 17,082,927 input tokens and
119,003 output tokens; summed trial wall time was about 1.17 hours. These
usage figures are descriptive and do not estimate Azure billing (Harbor
reported cost as zero for this deployment). Four tasks per language is too
small for a language comparison.

The pattern is not merely “the agent needed more time.” Some attempts reached
the full limit, while six ended earlier on malformed calls and three reached
the submit marker with an empty patch. The one verifier-passing unsubmitted
working tree is especially important: repair quality and agent protocol
completion are separate failure modes. A longer cap or an automatic patch
submission would define a different agent configuration and needs a new,
separately declared experiment; it cannot be retroactively substituted into
this calibration.

## Audit and reproduction

The machine-readable per-task record is
`audit/gpt_oss_20b/calibration_results.json`. It includes the raw reward,
submit-marker and patch-submission flags, agent exit, calls, tokens, elapsed
time, error count, and trial/trajectory IDs with trajectory SHA-256 hashes.
Raw trajectories and verifier logs remain in ignored
`results/harbor_jobs_host/` and are not pushed; they may contain task source
or sensitive runtime content. All 12 Harbor jobs completed one trial with
zero infrastructure exceptions and zero retries. The first eight tasks were
run in sequence; the runner stopped after detecting the reward/submission
discrepancy, and only the four untouched IDs were resumed after the scorer
was clarified. No completed task was rerun.

Regenerate the JSON summary from retained local Harbor jobs with
`python -m src.gpt_oss_calibration_report`. The run configuration and
predeclared scoring rule are documented in
`docs/gpt_oss_20b_run_policy.md`. The frozen pilot partition remains
unconsumed. No graph-vs-flat-vs-no-memory outcome can be inferred from these
no-memory-only data.
