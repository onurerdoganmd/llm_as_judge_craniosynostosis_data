"""
TypeSafe Jev API adapter (Phase 3).

Built against docs.typesafe.ai (api.md, primitives/score.md, primitives/noul.md,
models.md; read 2026-10-10):
  POST https://api.typesafe.ai/v1/systemone   {state, model, questions}
  GET  https://api.typesafe.ai/v1/models
  Auth: "Authorization: Bearer <key>"
  Errors: 401 auth, 422 validation (not retried), 429 rate limit, 529 overloaded.

Rules implemented here (brief Parts 21-22):
- the raw response is written to disk BEFORE parsing; files are never overwritten;
- bounded retries with exponential backoff + jitter, only for transient failures;
  a valid answer is never re-requested;
- the API key is never written anywhere; raw records are checked for it;
- TLS is always verified (OS trust store via truststore; certifi lacks the
  issuing root on this machine, audit note in QC report).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from jev_extension import data_contract as dc
from jev_extension.spec import build_spec as bs

API_BASE = "https://api.typesafe.ai"
EVALUATE_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
TYPE_NAMES = {"score": "score", "boolean": "noul"}
RETRYABLE_STATUS = {429, 529} | set(range(500, 600))
RECORDED_RESPONSE_HEADERS = {"x-typesafe-request-id", "date", "content-type", "retry-after",
                             "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"}


class JevConfigError(RuntimeError):
    pass


# ── configuration ────────────────────────────────────────────────────────────

def load_api_key() -> str:
    """Key from the environment, else from jev_extension/.env.jev (git-ignored)."""
    name = dc.load_config()["jev"]["api_key_env_var"]
    if not name:
        raise JevConfigError("jev.api_key_env_var not set in frozen_config.yaml")
    if os.environ.get(name):
        return os.environ[name].strip()
    env_file = dc.ROOT / "jev_extension" / ".env.jev"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    raise JevConfigError(f"{name} not found in environment or jev_extension/.env.jev")


def make_ssl_context() -> ssl.SSLContext:
    import truststore
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)   # verification on, OS trust store


# ── request construction ─────────────────────────────────────────────────────

def build_payload(spec: dict, mode: str, row, model: str) -> dict:
    """Request body for one response x mode. Pure function of (spec, mode, response, model)."""
    state = spec["context"]["rubric"] + "\n\n" + bs.case_text(row)
    questions = {}
    for d in bs.decisions_for(spec, mode, row["question_id"]):
        q = {"type": TYPE_NAMES[d["type"]], "instructions": d["question"]}
        if d["type"] == "score":
            q["criteria"] = list(d["criteria"])
        questions[d["id"]] = q
    return {"state": state, "model": model, "questions": questions}


def canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


# ── transport with retries ───────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    max_retries: int = 4
    base_seconds: float = 2.0
    max_seconds: float = 32.0

    @classmethod
    def from_config(cls) -> "RetryPolicy":
        r = dc.load_config()["jev"]["retry"]
        return cls(r["max_retries"], r["backoff_base_seconds"], r["backoff_max_seconds"])

    def delay(self, retry_index: int, retry_after: str | None, rng: random.Random) -> float:
        d = min(self.max_seconds, self.base_seconds * 2 ** retry_index) * rng.uniform(0.5, 1.0)
        if retry_after:
            try:
                d = max(d, min(self.max_seconds, float(retry_after)))
            except ValueError:
                pass
        return d


@dataclass
class CallRecord:
    """Everything known about one logical call, written to disk before parsing."""
    kind: str
    request_utc: str
    url: str
    request_body: dict | None
    request_sha256: str | None
    attempts: list = field(default_factory=list)
    status: str = "pending"          # success | failed_non_retryable | failed_retries_exhausted
    status_code: int | None = None
    response_headers: dict = field(default_factory=dict)
    body_text: str | None = None
    meta: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, ensure_ascii=False) + "\n"


def _is_malformed(kind: str, text: str) -> str | None:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return "body is not JSON"
    if kind == "evaluate" and not (isinstance(body, dict) and isinstance(body.get("answers"), dict)):
        return "missing 'answers' object"
    if kind == "models" and not (isinstance(body, dict) and isinstance(body.get("models"), list)):
        return "missing 'models' list"
    return None


class JevClient:
    def __init__(self, api_key: str, *, timeout: float = 60.0, retry: RetryPolicy | None = None,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 rng_seed: int | None = None):
        self._key = api_key
        self.retry = retry or RetryPolicy.from_config()
        self._sleep = sleep
        self._rng = random.Random(rng_seed)     # jitter only; never affects Jev
        kwargs = {"transport": transport} if transport else {"verify": make_ssl_context()}
        self._http = httpx.Client(base_url=API_BASE, timeout=timeout,
                                  headers={"Authorization": f"Bearer {api_key}",
                                           "Content-Type": "application/json"}, **kwargs)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _call(self, kind: str, method: str, path: str, body: dict | None) -> CallRecord:
        rec = CallRecord(kind=kind, request_utc=datetime.now(timezone.utc).isoformat(), url=API_BASE + path,
                         request_body=body, request_sha256=canonical_sha256(body) if body is not None else None)
        for attempt in range(self.retry.max_retries + 1):
            t0 = time.perf_counter()
            entry = {"attempt": attempt, "utc": datetime.now(timezone.utc).isoformat()}
            retry_after = None
            try:
                resp = self._http.request(method, path, json=body)
                entry["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                entry["status_code"] = resp.status_code
                rec.status_code = resp.status_code
                rec.body_text = resp.text
                rec.response_headers = {k: v for k, v in resp.headers.items()
                                        if k.lower() in RECORDED_RESPONSE_HEADERS}
                entry["request_id"] = resp.headers.get("x-typesafe-request-id")
                retry_after = resp.headers.get("retry-after")
                if resp.status_code == 200:
                    problem = _is_malformed(kind, resp.text)
                    if problem is None:
                        entry["outcome"] = "success"
                        rec.attempts.append(entry)
                        rec.status = "success"
                        return rec
                    entry["outcome"] = f"malformed_response: {problem}"
                elif resp.status_code in RETRYABLE_STATUS:
                    entry["outcome"] = f"http_{resp.status_code}"
                else:
                    entry["outcome"] = f"http_{resp.status_code} (not retried)"
                    rec.attempts.append(entry)
                    rec.status = "failed_non_retryable"
                    return rec
            except httpx.TimeoutException as e:
                entry.update(latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                             outcome=f"timeout: {type(e).__name__}")
            except httpx.TransportError as e:
                entry.update(latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                             outcome=f"connection_error: {type(e).__name__}: {e}")
            rec.attempts.append(entry)
            if attempt < self.retry.max_retries:
                wait = self.retry.delay(attempt, retry_after, self._rng)
                entry["backoff_s"] = round(wait, 2)
                self._sleep(wait)
        rec.status = "failed_retries_exhausted"
        return rec

    def discover_models(self) -> CallRecord:
        return self._call("models", "GET", MODELS_PATH, None)

    def evaluate(self, payload: dict) -> CallRecord:
        return self._call("evaluate", "POST", EVALUATE_PATH, payload)

    def assert_no_secret(self, text: str) -> None:
        if self._key and self._key in text:
            raise RuntimeError("raw record would contain the API key; refusing to write")


# ── raw preservation ─────────────────────────────────────────────────────────

def write_raw(client: JevClient, rec: CallRecord, directory: Path, stem: str) -> Path:
    """Exclusive-create the raw record and append its hash to directory/raw_index.jsonl."""
    directory.mkdir(parents=True, exist_ok=True)
    text = rec.to_json()
    client.assert_no_secret(text)
    path = directory / f"{stem}.json"
    with open(path, "x", encoding="utf-8", newline="\n") as f:       # never overwrite
        f.write(text)
    entry = {"file": path.name, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
             "status": rec.status, "request_sha256": rec.request_sha256, **rec.meta}
    with open(directory / "raw_index.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def raw_stem(response_id: str, run_id: str, mode: str, model: str, utc: datetime | None = None) -> str:
    utc = utc or datetime.now(timezone.utc)
    return f"{response_id}__{run_id}__{mode}__{utc.strftime('%Y%m%dT%H%M%S%fZ')}__{model}"


# ── parsing / validation (operates on the saved body text only) ──────────────

def parse_answers(body_text: str, decisions: list, pinned_model: str) -> dict:
    """Validate one evaluate response. Returns {'model', 'model_ok', 'usage', 'decisions': [...]}."""
    pv = dc.load_config()["jev"]["probability_validation"]
    body = json.loads(body_text)
    answers = body.get("answers", {})
    out = {"model": body.get("model"), "model_ok": body.get("model") == pinned_model,
           "usage": body.get("usage"), "decisions": []}
    for d in decisions:
        row = {"decision_id": d["id"], "type": d["type"], "valid": False, "error": None}
        a = answers.get(d["id"])
        expected_type = TYPE_NAMES[d["type"]]
        if a is None:
            row["error"] = "missing answer"
        elif a.get("type") != expected_type:
            row["error"] = f"type {a.get('type')!r} != {expected_type!r}"
        elif d["type"] == "boolean":
            p = a.get("noul")
            if isinstance(p, (int, float)) and 0.0 <= p <= 1.0:
                row.update(valid=True, p_true=float(p))
            else:
                row["error"] = f"invalid noul {p!r}"
        else:
            row.update(_validate_score(a, len(d["criteria"]), pv))
        out["decisions"].append(row)
    return out


def _validate_score(a: dict, n_levels: int, pv: dict) -> dict:
    probs = a.get("probabilities")
    keys = [str(k) for k in range(n_levels)]
    if not isinstance(probs, dict) or sorted(probs, key=int) != keys:
        return {"error": f"probability keys {sorted(probs) if isinstance(probs, dict) else probs!r} != 0..{n_levels - 1}"}
    p = [probs[k] for k in keys]
    if not all(isinstance(x, (int, float)) and 0.0 <= x <= 1.0 for x in p):
        return {"error": "probability outside [0,1]"}
    raw_sum = float(sum(p))
    res = {"prob_sum_raw": raw_sum, "renormalised": False}
    if abs(raw_sum - 1.0) > pv["sum_tolerance_accept"]:
        if abs(raw_sum - 1.0) > pv["sum_tolerance_renormalise"]:
            return {**res, "error": f"probabilities sum to {raw_sum}"}
        p = [x / raw_sum for x in p]
        res["renormalised"] = True
    expected = sum(k * x for k, x in enumerate(p))
    modal = max(range(n_levels), key=lambda k: p[k])
    if not 0.0 <= expected <= n_levels - 1:
        return {**res, "error": f"expected score {expected} out of range"}
    return {**res, "valid": True, "probs": p, "expected_score": expected, "modal_score": modal,
            "api_score": a.get("score"), "confidence": a.get("confidence"),
            "api_score_matches": a.get("score") is None
                                 or abs(a["score"] - expected) <= pv["api_score_tolerance"]}
