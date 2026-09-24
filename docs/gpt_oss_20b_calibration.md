# gpt-oss-20b calibration

Status: pending Foundry endpoint and Docker/Harbor availability. No calibration tasks have been run.

The frozen 12-task partition is in `audit/gpt_oss_20b/calibration_task_ids.json`. Run `no_memory` only, one attempt per task, through the official pinned July-2026 Harbor task and verifier. Keep the profile, reasoning effort, prompt, timeout, step limit, and tool access fixed. Record each task's language, repo, official resolved reward, exit status, model calls, tokens if available, wall time, tool calls, patch SHA256, trajectory path, and errors in `calibration_results.json`.

Decision rule fixed before outcomes: 0–2/12 suggests a floor effect; 3–9/12 is a useful range; 10–12/12 suggests a ceiling. Only the middle range permits consideration of the pilot, and only after the graph-readiness gate is met. This document must be updated with actual official rewards before any pilot run.
