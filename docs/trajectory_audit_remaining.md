# Trajectory audit — remaining smoke tasks

Observable behavior for the six runs (`sphinx-doc__sphinx-10449`, `django__django-11299` ×
`no_memory`, `flat_rag`, `graph`). Derived from `results/<condition>/<task>/trajectory.json`.
Attribution is conservative: "the condition retrieved X" is reported separately from what the agent
then did; no causal claim is made unless strongly supported.

Memory-linkage labels used below:
- **relevant** — retrieved memory clearly matches the change the agent made.
- **present but apparently unused** — memory was retrieved but no clear link to observed actions.
- **no clear link** — same as above, stated per run.

## sphinx-doc__sphinx-10449

### no_memory — 33 calls
- Files inspected, roughly in order: `sphinx/ext/autodoc/typehints.py`;
  `tests/test_ext_autodoc_configs.py`; `tests/roots/test-ext-autodoc/target/typehints.py`.
- Edit: in `merge_typehints`, drop the `'return'` annotation when `objtype == 'class'`.
- Backtracking/tooling: `apply_patch`, then an external `patch -p0`, then a Python
  text-replacement fallback.
- Verification before submit: built a minimal repro under `/tmp/sphinx_issue`, ran
  `sphinx-build -b text`, and inspected the generated `index.txt` for a `Return type` block. Did
  **not** run the project test suite.
- Memory: none (no-memory condition).

### flat_rag — 39 calls
- Files inspected, roughly in order: `sphinx/ext/autodoc/typehints.py`;
  `tests/test_ext_autodoc_configs.py` (several ranges); `sphinx/ext/autodoc/__init__.py`.
- Edit: in `record_typehints`, skip recording `'return'` when `objtype in ('class',
  'exception')`. **Also edited `tests/test_ext_autodoc_configs.py`** to remove the expected
  `Return type:` block.
- Verification before submit: ran
  `python -m pytest tests/test_ext_autodoc_configs.py -k documented_init -vv` (once).
- Repeated/unproductive actions: multiple Python snippets just to locate/adjust the expected test
  block (test-file bookkeeping, not new diagnosis).
- Memory linkage: retrieved memory was present (seeds `C012:message:86:call:0`, `C045:message:85`,
  `C069:message:39`, `C012:message:86`, `C045:message:84:call:0`) but **apparently unused** — no
  observable action tied to those documents.

### graph — 54 calls (most expensive run)
- Files inspected, roughly in order: `sphinx/ext/autodoc/typehints.py`;
  `tests/test_ext_autodoc_configs.py`; `tests/roots/test-ext-autodoc/target/typehints.py`;
  `CHANGES`.
- Edit: thread `objtype` into `modify_field_list`, skip the `rtype` field for classes, and fix the
  latent `annotation` → `annotations['return']` reference.
- Backtracking/tooling: applied the patch in `/workspace`, then `/testbed`; attempted
  `patch -p0`/`patch -p1`; later ran `git checkout -- sphinx/ext/autodoc/typehints.py` to reset and
  re-applied; iterated `pytest` roughly five times, reading the pytest build `index.txt` output.
- Verification before submit: repeated
  `python -m pytest tests/test_ext_autodoc_configs.py -k typehints_description_with_documented_init`
  plus inspection of the generated text output.
- Memory linkage: graph retrieved `sem:C012:evidence:86` (a provenance neighbor of a seed). The
  chosen fix (modify_field_list) differs from `no_memory`'s, but there is **no clear link** between
  the retrieved evidence and the observed edit.

## django__django-11299

### no_memory — 30 calls
- Files inspected, roughly in order: `django/db/models/constraints.py`;
  `django/db/backends/base/schema.py`; `django/db/models/sql/query.py`.
- Edit: pass `simple_col=simple_col` through the recursive `_add_q(...)` call.
- Verification before submit: `python -m compileall django/db/models/sql/query.py` only. Did **not**
  run the project test suite.

### flat_rag — 20 calls (fewest calls)
- Files inspected, roughly in order: `django/db/models/constraints.py`;
  `tests/constraints/tests.py`; `django/db/models/sql/query.py`.
- Edit: same `_add_q(..., simple_col=simple_col)` change.
- Verification before submit: ran `python -m pytest tests/constraints/tests.py` (once).
- Memory linkage: retrieved memory was present (seeds `C033:task`, `C033:message:1`,
  `sem:C033:task`, `C015:message:1`, `C039:message:1`) but **apparently unused**.

### graph — 24 calls
- Files inspected, roughly in order: `django/db/models/constraints.py`;
  `django/db/models/sql/query.py`; `django/db/models/expressions.py`.
- Edit: same `_add_q(..., simple_col=simple_col)` change.
- Backtracking/tooling: `apply_patch`, then `patch -p0`/`patch -p1`, then a Python text-replacement
  fallback.
- Verification before submit: none beyond `git diff`. Did **not** run the project test suite.
- Memory linkage: graph retrieved `sem:C015:evidence:1`, `sem:C033:evidence:1`,
  `sem:C039:evidence:1`, `sem:C033:assessment` (relations `ASSESSES_AGAINST` ×1,
  `CITES_SOURCE_MESSAGE` ×3). All three conditions produced a **byte-identical** patch, so there is
  **no clear link** between the retrieved evidence and the solution.

## Cross-run observations (descriptive)

- The strongest signal is the identical `django__django-11299` patch across all three conditions:
  memory (flat or graph) did not change the submitted change.
- `sphinx-doc__sphinx-10449` produced three different implementations that all resolved, i.e. the
  task admits multiple valid fixes; the graph run was the slowest and costliest (54 calls,
  $0.1655), which is descriptive at N=1 and not an efficiency claim.
- Only `flat_rag` on sphinx modified test files; the other five patches were source-only.
- Common tooling friction in several runs: `apply_patch` fallback to external `patch` / Python text
  replacement, and one `git checkout`/re-apply loop in the graph sphinx run.
- No run used the official SWE-bench tests before submission; test usage was limited to the repo's
  own test files (`pytest` in three runs) or a local repro (`sphinx-build` in one run).
- In all six runs the retrieved memory (when present) is best classified as **present but apparently
  unused / no clear link** to the later observed actions.
