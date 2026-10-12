"""
Stage 02: run Jev on the frozen benchmark (pilot or full).

    python -m jev_extension.run_jev --phase pilot --run 1
    python -m jev_extension.run_jev --phase full  --run 3 [--mode A_matched]

Guards before any request: frozen spec (assert_frozen), unchanged original files,
Phase 5 freeze lock intact (full phase only), pinned model id, model discovery saved. Writes only raw records + a run manifest;
never parses answers or reads expert data. Resumable: a (response, run, mode) that
already has a final record (success OR failure) is not called again
(brief Part 21: no repeated calling after the retry policy is exhausted).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from jev_extension import data_contract as dc
from jev_extension import freeze
from jev_extension import manifest as mf
from jev_extension.api import jev_client as jc
from jev_extension.spec import build_spec as bs

RAW = dc.ROOT / "jev_extension" / "raw"


def pilot_selection() -> list[str]:
    """Seeded draw stratified by contestant (>= 1 each); uses only IDs, never labels or scores."""
    p = dc.load_config()["jev"]["pilot"]
    bench = dc.load_benchmark()
    rng = random.Random(p["seed"])
    chosen = []
    for model_key in sorted(bench["model_key"].unique()):
        ids = sorted(bench.loc[bench.model_key == model_key, "response_id"])
        chosen.append(rng.choice(ids))
    rest = sorted(set(bench["response_id"]) - set(chosen))
    chosen += rng.sample(rest, p["n_responses"] - len(chosen))
    return sorted(chosen)


def assert_originals_unchanged() -> None:
    baseline = json.loads((dc.ROOT / "jev_extension/config/frozen_original_inputs.json").read_text())
    changed = [f for f, h in baseline["sha256"].items() if dc.sha256_file(dc.ROOT / f) != h]
    if changed:
        raise RuntimeError(f"original study files changed: {changed}")


def done_keys(index_path: Path) -> set:
    if not index_path.exists():
        return set()
    keys = set()
    for line in index_path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        keys.add((e.get("response_id"), e.get("run_id"), e.get("mode")))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["pilot", "full"], required=True)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--mode", choices=["A_matched", "B_native", "both"], default="both")
    args = ap.parse_args()

    cfg = dc.load_config()
    max_runs = cfg["jev"]["pilot"]["n_runs"] if args.phase == "pilot" else cfg["jev"]["n_repeat_runs"]
    if not 1 <= args.run <= max_runs:
        raise SystemExit(f"--run must be 1..{max_runs} for phase {args.phase}")
    lock = bs.assert_frozen()
    assert_originals_unchanged()
    phase5 = freeze.assert_phase5_frozen() if args.phase == "full" else None
    model = cfg["jev"]["model_id"]
    spec = json.loads(bs.SPEC_PATH.read_text(encoding="utf-8"))
    bench = dc.load_benchmark().set_index("response_id", drop=False)
    ids = pilot_selection() if args.phase == "pilot" else sorted(bench.index)
    modes = ["A_matched", "B_native"] if args.mode == "both" else [args.mode]
    run_id = f"run{args.run:02d}"
    run_dir = RAW / args.phase / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)

    with jc.JevClient(jc.load_api_key()) as client:
        disc = client.discover_models()
        disc.meta = {"purpose": "model discovery at run start", "phase": args.phase, "run_id": run_id}
        disc_path = jc.write_raw(client, disc, RAW / "model_metadata",
                                 f"models__{args.phase}_{run_id}__{started.strftime('%Y%m%dT%H%M%S%fZ')}")
        if disc.status != "success":
            raise RuntimeError("model discovery failed; not running")

        counts = {"called": 0, "skipped_existing": 0, "success": 0, "failed": 0, "retried": 0}
        for mode in modes:
            mode_dir = run_dir / mode
            done = done_keys(mode_dir / "raw_index.jsonl")
            for rid in ids:
                if (rid, run_id, mode) in done:
                    counts["skipped_existing"] += 1
                    continue
                payload = jc.build_payload(spec, mode, bench.loc[rid], model)
                rec = client.evaluate(payload)
                rec.meta = {"response_id": rid, "question_id": bench.loc[rid, "question_id"], "run_id": run_id,
                            "mode": mode, "phase": args.phase, "pinned_model": model,
                            "spec_version": lock["spec_version"], "wording_sha256": lock["wording_sha256"]}
                jc.write_raw(client, rec, mode_dir, jc.raw_stem(rid, run_id, mode, model))
                counts["called"] += 1
                counts["success" if rec.status == "success" else "failed"] += 1
                counts["retried"] += int(len(rec.attempts) > 1)

    run_manifest = mf.build_manifest()
    run_manifest["run"] = {"phase": args.phase, "run_id": run_id, "modes": modes, "response_ids": ids,
                           "started_utc": started.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
                           "counts": counts, "model_discovery_record": str(disc_path.relative_to(dc.ROOT)),
                           "spec_lock": lock,
                           "phase5_lock_sha256": freeze.sha256_lf(freeze.LOCK_PATH.relative_to(dc.ROOT)) if phase5 else None}
    run_manifest["jev"].update(model_id=model, endpoint=jc.API_BASE + jc.EVALUATE_PATH,
                               evaluation_spec_sha256=lock["spec_file_sha256"])
    text = json.dumps(run_manifest, indent=2, ensure_ascii=False) + "\n"
    client.assert_no_secret(text)
    out = dc.ROOT / "jev_extension" / "manifests" / f"run_{args.phase}_{run_id}__{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(out, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(json.dumps(counts), "->", out.relative_to(dc.ROOT))
    return 0 if counts["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
