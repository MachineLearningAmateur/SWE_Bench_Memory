"""Versioned import of the failure50 archive. Never executes or edits evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/failure_memory.yaml"
GUARD = "Historical model attribution, not proven cause. Preserve alternatives and recovered errors."


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def config(path=DEFAULT_CONFIG):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def rooted(path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def load_tokenizer(spec, **kwargs):
    # Resolve a public pinned snapshot explicitly. Loading the local path also
    # avoids Transformers' auxiliary metadata requests using an expired user token.
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer
    snapshot = snapshot_download(spec["name"], revision=spec["revision"], token=False,
                                 allow_patterns=["tokenizer*", "vocab*", "merges*", "special_tokens_map.json", "config.json"])
    return AutoTokenizer.from_pretrained(snapshot, local_files_only=True, **kwargs)


def source_path(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"Source path escapes archive: {relative}")
    return target


def audit_source(root, cfg):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if head != cfg["commit"]:
        raise ValueError(f"Unexpected source commit: {head}")
    for args in (["git", "diff", "--quiet", "HEAD", "--"],):
        if subprocess.run(args, cwd=root).returncode:
            raise ValueError("Source archive has modified tracked evidence")
    report = source_path(root, cfg["report"])
    rows = read_json(report)["rows"]
    manifests = [root / "dataset/manifest.json", root / "dataset/expansion_v1/manifest.json"]
    manifest_ids = [x["instance_id"] for p in manifests for x in read_json(p)["items"]]
    ids = [r["instance_id"] for r in rows]
    if len(ids) != 50 or len(set(ids)) != 50 or sorted(ids) != sorted(manifest_ids):
        raise ValueError("Expected the same 50 unique source tasks in both manifest stages")
    if sum(r["annotation"] is not None for r in rows) != 48:
        raise ValueError("Expected 48 annotations and two explicit deferrals")
    refs = {}
    for row in rows:
        if row["outcome"] != "MODEL_FAILURE":
            raise ValueError("Unexpected source outcome")
        for ref in row["artifacts"].values():
            if isinstance(ref, dict) and "path" in ref and "sha256" in ref:
                if ref["path"] in refs and refs[ref["path"]] != ref["sha256"]:
                    raise ValueError("Conflicting source hashes")
                refs[ref["path"]] = ref["sha256"]
    for relative, expected in refs.items():
        if digest(source_path(root, relative)) != expected:
            raise ValueError(f"Source hash mismatch: {relative}")
    return rows, {"commit": head, "report_sha256": digest(report),
                  "manifests": {str(p.relative_to(root)): digest(p) for p in manifests},
                  "artifacts": refs, "artifact_count": len(refs)}


def resolve_body(event, by_step, seen=None):
    seen = set() if seen is None else seen
    step = event["step_id"]
    if step in seen:
        raise ValueError("Cyclic repeated-observation reference")
    seen.add(step)
    if event.get("body") is not None:
        return event["body"]
    if event.get("same_as") is not None:
        return resolve_body(by_step[event["same_as"]], by_step, seen)
    return None


def event_text(event, by_step):
    body = resolve_body(event, by_step)
    if body is None:
        return "RECORDED TERMINAL STATE\n" + canonical(event.get("terminal", {}))
    out = []
    action = body.get("action", {})
    message = body.get("message", {}).get("content", "")
    if action.get("command") is not None:
        out.append("COMMAND\n" + str(action["command"]))
    if "done" in action:
        out.append(f"ACTOR DECLARED DONE: {action['done']}")
    # Exclude duplicate JSON serialization of the same action, retain other visible text.
    try:
        duplicate = json.loads(message) == action
    except (ValueError, TypeError):
        duplicate = False
    if message and not duplicate:
        out.append("VISIBLE RESPONSE\n" + str(message))
    result = body.get("result") or {}
    observation = result.get("observation")
    if observation is not None:
        if isinstance(observation, dict):
            out.append("FEEDBACK\n" + "\n".join(f"{k}: {v}" for k, v in observation.items()))
        else:
            out.append("FEEDBACK\n" + str(observation))
    if event.get("same_as") is not None:
        out.append(f"Repeated evidence from step {event['same_as']}; this occurrence is step {event['step_id']}.")
    return "\n\n".join(out)


def records_for_case(root, row):
    """Use the exact structured view the annotator saw, with raw-step provenance."""
    task_id = row["instance_id"]
    ref = row["artifacts"]["frontier_structured"]
    view = read_json(source_path(root, ref["path"]))
    normalized = view["source_normalized"]
    task = normalized["task"]
    events = view["events"]
    by_step = {e["step_id"]: e for e in events}
    if len(by_step) != len(events):
        raise ValueError("Duplicate evidence step")
    documents, edges = [], []
    annotation = row["annotation"]
    common_guard = GUARD if annotation else "Unannotated historical failed attempt; no diagnosis is available."

    def add(suffix, kind, body, steps=(), provenance=None, guard=""):
        node = f"{task_id}:{suffix}"
        documents.append({"id": node, "case_id": task_id, "repo": row["repo"], "kind": kind,
                          "text": body, "guard": guard,
                          "provenance": provenance or {"artifact": ref, "step_ids": list(steps)}})
        return node

    task_node = add("task", "task", task["problem_statement"], provenance={"artifact": ref, "field": "source_normalized.task"})
    event_nodes, aliases = {}, {}
    for event in events:
        step = event["step_id"]
        node = add(f"step:{step}", "terminal" if "terminal" in event else "event",
                   event_text(event, by_step), event.get("raw_step_ids", [step]))
        event_nodes[step] = node
        for raw_step in event.get("raw_step_ids", [step]):
            aliases[raw_step] = node
    if annotation:
        parsed = read_json(source_path(root, row["artifacts"]["parsed_annotation"]["path"]))
        if parsed != annotation:
            raise ValueError(f"Report annotation differs from original: {task_id}")
        guard_fields = {k: annotation[k] for k in ("annotation_status", "alternative_explanations", "notes", "evidence_quality")}
        sections = [("primary", annotation["primary_failure"], "IDENTIFIES_CRITICAL_STEP")]
        sections += [(f"secondary:{i}", a, "IDENTIFIES_CRITICAL_STEP") for i, a in enumerate(annotation["secondary_failures"])]
        sections += [(f"recovered:{i}", a, "DESCRIBES_INCIDENTAL_OR_RECOVERED_ERROR") for i, a in enumerate(annotation["incidental_or_recovered_errors"])]
        sections += [("terminal_assessment", annotation["final_failure_manifestation"], "DESCRIBES_TERMINAL_OUTCOME")]
        for suffix, finding, relation in sections:
            if finding is None:
                continue
            node = add(suffix, "annotation", canonical({"finding": finding, **guard_fields}),
                       provenance={"artifact": row["artifacts"]["parsed_annotation"], "section": suffix}, guard=common_guard)
            edges.append((task_node, "HAS_ANNOTATION", node))
            cited = set(finding.get("evidence_steps", []))
            critical = finding.get("critical_step", finding.get("step"))
            if critical is not None:
                cited.add(critical)
            for step in sorted(cited):
                target = event_nodes.get(step, aliases.get(step))
                if not target:
                    raise ValueError(f"Unresolved annotation reference: {task_id} step {step}")
                edges.append((node, relation if step == critical else "CITES_EVIDENCE", target))
        # Only cited historical evaluator summaries; never index full logs or test patches.
        if annotation["final_failure_manifestation"] and view.get("official_result"):
            node = add("postrun_evaluation", "postrun", "POST-RUN HISTORICAL EVALUATOR SUMMARY\n" + canonical(view["official_result"]))
            edges.append((f"{task_id}:terminal_assessment", "CITES_POSTRUN_SUMMARY", node))
    for i, (source, relation, target) in enumerate(sorted(set(edges))):
        add(f"relation:{i}", "relation", f"{source} --{relation}--> {target}",
            provenance={"source": source, "target": target, "relation": relation}, guard=common_guard)
    return {"id": task_id, "repo": row["repo"], "problem_statement": task["problem_statement"],
            "annotated": annotation is not None}, documents, sorted(set(edges))


def chunk_spans(text, tokenizer, size, overlap):
    if not 0 <= overlap < size:
        raise ValueError("Invalid chunk overlap")
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets = encoded["offset_mapping"]
    if not offsets:
        return
    start = 0
    while start < len(offsets):
        end = min(start + size, len(offsets))
        left = 0 if start == 0 else offsets[start][0]
        right = len(text) if end == len(offsets) else offsets[end][0]
        # BPE byte pieces can share a Unicode character; preserve complete characters.
        piece = text[left:right]
        while len(tokenizer.encode(piece, add_special_tokens=False)) > size and end > start + 1:
            end -= 1
            right = offsets[end][0]
            piece = text[left:right]
        if piece:
            yield left, right, piece
        if end >= len(offsets):
            break
        start = max(start + 1, end - overlap)


def build(cfg, output=None, tokenizer=None):
    root = rooted(cfg["source"]["path"])
    out = rooted(output or cfg["corpus"])
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite corpus: {out}")
    rows, audit = audit_source(root, cfg["source"])
    if tokenizer is None:
        tokenizer = load_tokenizer(cfg["models"]["embedding"])
    out.mkdir(parents=True)
    db = sqlite3.connect(out / "corpus.sqlite")
    try:
        db.executescript("""
        CREATE TABLE cases(id TEXT PRIMARY KEY, repo TEXT, problem_statement TEXT, annotated INTEGER);
        CREATE TABLE documents(id TEXT PRIMARY KEY, case_id TEXT, repo TEXT, kind TEXT, text TEXT, guard TEXT, provenance TEXT);
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE, doc_id TEXT, start_char INTEGER, end_char INTEGER, text TEXT);
        CREATE INDEX chunk_doc ON chunks(doc_id);
        CREATE TABLE edges(source TEXT, relation TEXT, target TEXT, PRIMARY KEY(source,relation,target));
        CREATE VIRTUAL TABLE fts USING fts5(text,content='chunks',content_rowid='id',tokenize='unicode61');
        """)
        for row in sorted(rows, key=lambda r: r["instance_id"]):
            case, documents, edges = records_for_case(root, row)
            db.execute("INSERT INTO cases VALUES(:id,:repo,:problem_statement,:annotated)", case)
            for doc in documents:
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?)", tuple(doc[k] for k in ("id", "case_id", "repo", "kind", "text", "guard")) + (canonical(doc["provenance"]),))
                for part, (left, right, text) in enumerate(chunk_spans(doc["text"], tokenizer, cfg["retrieval"]["chunk_tokens"], cfg["retrieval"]["overlap_tokens"])):
                    db.execute("INSERT INTO chunks(chunk_id,doc_id,start_char,end_char,text) VALUES(?,?,?,?,?)", (f"{doc['id']}#{part}", doc["id"], left, right, text))
            db.executemany("INSERT INTO edges VALUES(?,?,?)", edges)
        db.execute("INSERT INTO fts(fts) VALUES('rebuild')")
        db.commit()
        counts = {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in ("cases", "documents", "chunks", "edges")}
    finally:
        db.close()
    manifest = {"version": 1, "source": audit, "config": cfg, "counts": counts,
                "corpus_sha256": digest(out / "corpus.sqlite"), "builder_sha256": digest(__file__)}
    write_json(out / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(config(args.config), args.output)["counts"]))


if __name__ == "__main__":
    main()
