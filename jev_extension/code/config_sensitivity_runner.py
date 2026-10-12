"""
Configuration-sensitivity study runner (protocol §2, §5, §6, §9).

    python -m jev_extension.config_sensitivity.runner --preflight
    python -m jev_extension.config_sensitivity.runner --run 1          # all arms, run 1 (A,B,C,D1,D2)

Guards before any call: Phase 5 lock intact, CS lock intact, original files unchanged, pinned model.
Uses the frozen JevClient unchanged; the bytes actually sent are captured with an httpx request
hook and must equal the expected order-preserving serialisation. Raw records are exclusive-create,
never overwritten; resumable (a final record, success or failure, is never re-requested).
Never parses answer values and never touches expert data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

from jev_extension import data_contract as dc
from jev_extension import freeze
from jev_extension.api import jev_client as jc
from jev_extension.config_sensitivity import builders as B
from jev_extension.config_sensitivity import freeze_cs
from jev_extension.config_sensitivity.validate import RAW, body_bytes, ordered_sha256
from jev_extension.run_jev import assert_originals_unchanged
from jev_extension.spec import build_spec as bs

N_RUNS = 3
MANIFEST_DIR = dc.ROOT / "jev_extension" / "manifests"
# Synthetic, non-study content for the technical preflight (protocol §5). Not a benchmark response.
PREFLIGHT_ROW = {"response_id": "PREFLIGHT_synthetic", "question_id": "FAQ_06", "category": "synthetic",
                 "question": "Synthetic preflight question: is this request well formed?",
                 "response": "Synthetic preflight answer used only to check the request and response schema."}
PREFLIGHT_PLAN = [("B", 0), ("C", 1), ("D1", 0), ("D2", 0)]      # C part 1 = safety request


def option_order(payload: dict) -> dict | None:
    qs = payload["questions"]
    if not any(q["type"] == "choice" for q in qs.values()):
        return None
    return {qid: list(q["criteria"]) for qid, q in qs.items()}


def _call(client, captured, payload, meta, directory, stem_mode, rid, run_id, model):
    expected = body_bytes(payload)
    captured.clear()
    rec = client.evaluate(payload)
    sent = captured[-1] if captured else None
    rec.meta = {**meta, "request_sha256_ordered": hashlib.sha256(expected).hexdigest(),
                "request_sha256_sent": hashlib.sha256(sent).hexdigest() if sent is not None else None,
                "request_body_serialized": expected.decode("utf-8"),
                "pinned_model": model, "cs_protocol": "CONFIG_SENSITIVITY_PROTOCOL v1.0"}
    oo = option_order(payload)
    if oo:
        rec.meta["option_order_sent"] = oo
    if sent is not None and sent != expected:
        raise RuntimeError("outbound bytes differ from the expected order-preserving serialisation")
    jc.write_raw(client, rec, directory, jc.raw_stem(rid, run_id, stem_mode, model))
    return rec


def _done(directory) -> set:
    idx = directory / "raw_index.jsonl"
    if not idx.exists():
        return set()
    return {(e["response_id"], e.get("part", 0)) for e in map(json.loads, idx.read_text(encoding="utf-8").splitlines())}


def _client_with_capture(key):
    client = jc.JevClient(key)
    captured = []
    client._http.event_hooks["request"].append(lambda req: captured.append(req.content))  # observe only
    return client, captured


def lock_hashes() -> dict:
    return {"cs_lock_sha256": freeze.sha256_lf(freeze_cs.LOCK_PATH.relative_to(dc.ROOT)),
            "phase5_lock_sha256": freeze.sha256_lf(freeze.LOCK_PATH.relative_to(dc.ROOT))}


def guards() -> dict:
    freeze.assert_phase5_frozen()
    lock = freeze_cs.assert_cs_frozen()
    assert_originals_unchanged()
    if dc.load_config()["jev"]["model_id"] != lock["model_id"]:
        raise RuntimeError("pinned model differs from CS lock")
    return lock


def preflight(key=None) -> dict:
    lock = guards()
    spec = json.loads(bs.SPEC_PATH.read_text(encoding="utf-8"))
    model = lock["model_id"]
    out_dir = RAW / "preflight"
    client, captured = _client_with_capture(key or jc.load_api_key())
    results = []
    with client:
        for arm, part in PREFLIGHT_PLAN:
            payload = B.build(arm, spec, PREFLIGHT_ROW, model)[part]
            meta = {"arm": arm, "part": part, "run_id": "preflight", "response_id": PREFLIGHT_ROW["response_id"],
                    "question_id": PREFLIGHT_ROW["question_id"], "phase": "preflight"}
            rec = _call(client, captured, payload, meta, out_dir / arm, f"CS-{arm}-p{part}",
                        PREFLIGHT_ROW["response_id"], "preflight", model)
            results.append({"arm": arm, "status": rec.status, "http": rec.status_code})
    return {"calls": results}


def run(run_no: int, key=None) -> dict:
    if not 1 <= run_no <= N_RUNS:
        raise SystemExit(f"--run must be 1..{N_RUNS}")
    lock = guards()
    spec = json.loads(bs.SPEC_PATH.read_text(encoding="utf-8"))
    model = lock["model_id"]
    bench = dc.load_benchmark().set_index("response_id", drop=False)
    run_id = f"run{run_no:02d}"
    client, captured = _client_with_capture(key or jc.load_api_key())
    summary = {}
    with client:
        for arm in B.ARMS:
            started = datetime.now(timezone.utc)
            directory = RAW / "study" / arm / run_id
            disc = client.discover_models()
            disc.meta = {"purpose": "model discovery at arm-run start", "arm": arm, "run_id": run_id}
            disc_path = jc.write_raw(client, disc, RAW / "study" / "model_metadata",
                                     f"models__{arm}_{run_id}__{started.strftime('%Y%m%dT%H%M%S%fZ')}")
            if disc.status != "success":
                raise RuntimeError("model discovery failed; stopping")
            done = _done(directory)
            counts = {"called": 0, "skipped_existing": 0, "success": 0, "failed": 0}
            for rid in sorted(bench.index):
                row = bench.loc[rid]
                for part, payload in enumerate(B.build(arm, spec, row, model)):
                    if (rid, part) in done:
                        counts["skipped_existing"] += 1
                        continue
                    dim = B.DIMS[part] if arm == "C" else None
                    meta = {"arm": arm, "part": part, "dim": dim, "run_id": run_id, "response_id": rid,
                            "question_id": row["question_id"], "phase": "config_sensitivity"}
                    rec = _call(client, captured, payload, meta, directory,
                                f"CS-{arm}" + (f"-{dim}" if dim else ""), rid, run_id, model)
                    counts["called"] += 1
                    counts["success" if rec.status == "success" else "failed"] += 1
            man = {"study": "config_sensitivity", "arm": arm, "run_id": run_id, "counts": counts,
                   "started_utc": started.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
                   "model_discovery_record": str(disc_path.relative_to(dc.ROOT) if disc_path.is_relative_to(dc.ROOT) else disc_path),
                   **lock_hashes()}
            text = json.dumps(man, indent=2) + "\n"
            client.assert_no_secret(text)
            path = MANIFEST_DIR / f"config_sensitivity_{arm}_{run_id}__{started.strftime('%Y%m%dT%H%M%SZ')}.json"
            with open(path, "x", encoding="utf-8", newline="\n") as f:
                f.write(text)
            summary[arm] = counts
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--preflight", action="store_true")
    g.add_argument("--run", type=int)
    a = ap.parse_args()
    print(json.dumps(preflight() if a.preflight else run(a.run), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
