# Predeclared July SWE-rebench agent-run policy

Frozen before calibration or pilot outcomes on 2026-09-28. Earlier
retrieval-development trials used a 60-step diagnostic limit and are not
calibration results.

The benchmark uses one model-backed attempt per task and condition. The
same mini-SWE-agent 2.4.6 scaffold, Azure gpt-oss-20b deployment,
temperature 0, medium reasoning effort, bash tool contract, static memory
wrapper, and **100-step limit** apply to no memory, flat RAG, and graph.
The previous 60-step limit was an initial diagnostic choice, not a
literature standard. One published SWE-rebench/OpenHands comparison reports
100- and 500-turn settings; mini-SWE-agent's own SWE-bench configuration
uses 250. The 100-step choice here is a fixed, bounded first study, not a
claim of equivalence to either paper's agent or model.

A task that reaches the step limit, emits malformed calls, or ends without
an explicit mini-SWE-agent submission remains in the denominator and is
scored unresolved. Record its exit status and failure
category separately. Do not extend a live run, retry a model-behavior
failure, infer submission from prose, or recover and submit an unsubmitted
working-tree patch. A genuine infrastructure failure must be identified
separately before any retry decision; never silently replace an attempt.

No reminder or end-of-run prompt has been added. The official Harbor
verifier remains the correctness source. The 12-task no-memory calibration
still uses the handoff's 0–2 / 3–9 / 10–12 decision gate before any
held-out three-arm pilot.

During calibration, Harbor rewarded one unsubmitted working tree. Its raw
official reward is preserved in the audit, but the task remains unresolved
under the submit-required rule above. This clarifies the distinction between
verifier correctness and protocol completion; it does not change the rule or
agent behavior after seeing an outcome.

Run the frozen no-memory calibration with:

1. python -m src.gpt_oss_harbor_prepare --split calibration --dry-run
2. python -m src.gpt_oss_harbor_calibration (read-only preflight)
3. python -m src.gpt_oss_harbor_calibration --run (12 sequential paid trials)

The runner forces UTF-8 for Harbor's Windows console, verifies the staged
config hash and exact command for each task, refuses an existing job, stops
on infrastructure errors, and prints key-free per-trial summaries. If a
batch is interrupted, inspect the existing job before selecting only
untouched task IDs with repeated --task-id; do not rerun a completed task.

A no-model-call test confirmed that mini-SWE-agent 2.4.6 accepts the
explicit submit marker. A separate development-only 100-step run on
marimo-team__marimo-9754 ended LimitsExceeded, submitted nothing,
and received official reward 0. It is recorded as an unresolved diagnostic
in audit/gpt_oss_20b/development_100_step_recheck.json, not calibration.

Sources: [SWE-agent NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf),
[SWE-rebench/OpenHands 100- and 500-turn study](https://huggingface.co/nebius/SWE-rebench-openhands-Qwen3-30B-A3B/blob/614d0184b295a9484a72a9d3cf0a2538fe20a46d/README.md),
[mini-SWE-agent SWE-bench config](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/config/benchmarks/swebench.yaml).
