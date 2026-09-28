# July SWE-rebench Harbor staging

`python -m src.gpt_oss_harbor_prepare --task-id ludo-technologies__pyscn-548 --dry-run`
stages all three conditions for one retrieval-development task and asks Harbor to
validate their metadata without starting a model trial. Repeat `--task-id` for
more development tasks, or use `--condition` to select an arm. The command
rejects calibration, pilot, and reserve IDs.

Each arm writes an immutable instruction addendum, exact retrieval record, and
key-free Harbor command under `results/gpt_oss_staged/development/`. The addendum
is identical across arms except for the historical-memory field. Flat RAG and
graph use the same frozen SQLite corpus, exclude the current repository, and
share a 24,000-character budget. Graph uses the frozen graph-v2 policy. The
no-memory arm has an empty memory field. All three commands select the same
mini-SWE-agent 2.4.6 adapter and model settings.

Staging and `--dry-run` do not satisfy the agent-protocol gate. A model-backed
development trial is permitted for diagnosis, but any no-tool response or
malformed call outside the narrowly approved repair remains a format failure.
Do not reinterpret prose as a submission. Do not launch calibration or pilot
trials until the handoff's plumbing and calibration gates pass.

On 2026-09-28, three development tasks (one each in Python, TypeScript, and
Go) were staged in all three arms, and all nine Harbor dry-runs passed. One
model-backed no-memory development trial on `ludo-technologies__pyscn-548`
then made 56 valid shell calls and four malformed calls (two invalid JSON
argument strings and two unknown tool names), hit the 60-step limit, and
submitted no patch. The official verifier returned 0. This is a failed
plumbing gate, not evidence that the no-memory condition cannot solve the
task. The flat-RAG and graph arms remain unrun. See
`audit/gpt_oss_20b/development_staging_gate.json` for the key-free record.
