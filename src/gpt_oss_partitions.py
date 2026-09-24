"""Freeze task-only July 2026 SWE-rebench partitions before model outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from .retrieval_exposure_audit import MEMORY_REPOSITORIES, _canonical_task_list_sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "audit" / "retrieval_exposure"
DEST = ROOT / "audit" / "gpt_oss_20b"
LANGUAGES = ("python", "typescript", "go")
CALIBRATION_PER_LANGUAGE = 4
PILOT_PER_LANGUAGE = 8
SEED = 20260923


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _source_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _choose_language(tasks: list[dict], rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    """Keep repositories in one arm; minimize repeats within arms."""
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for task in sorted(tasks, key=lambda t: t["instance_id"]):
        by_repo[task["repo"]].append(task)
    repos = sorted(by_repo)
    rng.shuffle(repos)
    if len(repos) < CALIBRATION_PER_LANGUAGE + 1:
        raise ValueError("Not enough repositories to keep calibration and pilot disjoint")

    # Prefer singleton repositories for calibration so multi-task repositories
    # remain available to fill a pilot whose task quota exceeds its repo count.
    repos.sort(key=lambda repo: len(by_repo[repo]))
    calibration_repos = repos[:CALIBRATION_PER_LANGUAGE]
    pilot_repos = repos[CALIBRATION_PER_LANGUAGE:CALIBRATION_PER_LANGUAGE + PILOT_PER_LANGUAGE]
    calibration = [by_repo[repo][0] for repo in calibration_repos]
    pilot = [by_repo[repo][0] for repo in pilot_repos]
    if len(pilot) < PILOT_PER_LANGUAGE:
        extras = [task for repo in pilot_repos for task in by_repo[repo][1:]]
        pilot.extend(extras[:PILOT_PER_LANGUAGE - len(pilot)])
    if len(pilot) != PILOT_PER_LANGUAGE:
        raise ValueError("Cannot fill pilot quota without crossing calibration repositories")
    selected = {task["instance_id"] for task in calibration + pilot}
    reserve = [task for task in tasks if task["instance_id"] not in selected]
    return calibration, pilot, reserve


def build_partitions(tasks: list[dict], dev_ids: list[str], source: dict, *, seed: int = SEED) -> dict:
    ids = [t["instance_id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task IDs in source snapshot")
    if _canonical_task_list_sha256(tasks) != source["task_metadata_sha256"]:
        raise ValueError("Source task metadata does not match pinned benchmark hash")
    if not set(dev_ids) <= set(ids):
        raise ValueError("Development IDs absent from pinned source snapshot")
    excluded_memory = sorted({t["repo"] for t in tasks if t["repo"].lower() in MEMORY_REPOSITORIES})
    eligible = [t for t in tasks if t["language"] in LANGUAGES
                and t["instance_id"] not in dev_ids
                and t["repo"].lower() not in MEMORY_REPOSITORIES]
    rng = random.Random(seed)
    calibration: list[dict] = []
    pilot: list[dict] = []
    reserve: list[dict] = []
    for language in LANGUAGES:
        arm_a, arm_b, rest = _choose_language(
            [t for t in eligible if t["language"] == language], rng
        )
        calibration.extend(arm_a)
        pilot.extend(arm_b)
        reserve.extend(rest)
    cal_repos = {t["repo"] for t in calibration}
    pilot_repos = {t["repo"] for t in pilot}
    if cal_repos & pilot_repos:
        raise AssertionError("Calibration and pilot repositories overlap")
    if len(calibration) != 12 or len(pilot) != 24:
        raise AssertionError("Incorrect partition sizes")
    if len(calibration) + len(pilot) + len(reserve) != len(eligible):
        raise AssertionError("Eligible task lost from partitions")
    counts = {
        name: {"tasks": len(rows), "languages": dict(sorted(Counter(t["language"] for t in rows).items())),
               "repository_count": len({t["repo"] for t in rows})}
        for name, rows in (("calibration", calibration), ("pilot", pilot), ("reserve", reserve))
    }
    repeated = {
        name: {repo: n for repo, n in sorted(Counter(t["repo"] for t in rows).items()) if n > 1}
        for name, rows in (("calibration", calibration), ("pilot", pilot))
    }
    manifest = {
        "dataset": source["dataset"], "dataset_url": source["dataset_url"],
        "dataset_ref": source["dataset_ref"],
        "task_metadata_sha256": source["task_metadata_sha256"],
        "seed": seed, "primary_languages": list(LANGUAGES),
        "excluded_retrieval_dev_ids": sorted(dev_ids),
        "excluded_historical_memory_repositories": sorted(MEMORY_REPOSITORIES),
        "historical_memory_repositories_in_source": excluded_memory,
        "eligible_task_count": len(eligible), "partitions": counts,
        "calibration_pilot_repository_overlap": sorted(cal_repos & pilot_repos),
        "grouping_deviation": "One repeated Go repository within pilot is required: 11 eligible Go repositories for 12 task slots.",
        "repeated_repositories_within_arms": repeated,
        "pilot_condition_order": {
            t["instance_id"]: [["no_memory", "flat_rag", "graph"],
                               ["flat_rag", "graph", "no_memory"],
                               ["graph", "no_memory", "flat_rag"]][i % 3]
            for i, t in enumerate(pilot)
        },
    }
    return {"calibration": calibration, "pilot": pilot, "reserve": reserve, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check frozen files without changing them")
    args = parser.parse_args()
    partitions = build_partitions(
        _source_tasks(SOURCE / "source_tasks.jsonl"),
        _load_json(SOURCE / "dev_task_ids.json"),
        _load_json(SOURCE / "benchmark_source.json"),
    )
    partitions["manifest"]["source_jsonl_sha256"] = hashlib.sha256(
        (SOURCE / "source_tasks.jsonl").read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    files = {
        DEST / f"{name}_task_ids.json": [t["instance_id"] for t in partitions[name]]
        for name in ("calibration", "pilot", "reserve")
    }
    files[DEST / "partition_manifest.json"] = partitions["manifest"]
    for path, payload in files.items():
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Frozen partition differs: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Refusing to overwrite frozen partition: {path}")
            path.write_text(content, encoding="utf-8")
    print(f"calibration={len(partitions['calibration'])} pilot={len(partitions['pilot'])} reserve={len(partitions['reserve'])}")


if __name__ == "__main__":
    main()
