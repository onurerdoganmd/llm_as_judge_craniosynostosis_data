"""
Post-hoc configuration-sensitivity study: request builders for arms A, B, C, D1, D2.
(CONFIG_SENSITIVITY_PROTOCOL.md §3.) Pure functions of (frozen spec, benchmark row, model).

Every piece of rubric text is cut VERBATIM from the frozen rubric (spec.context.rubric =
prompts.JUDGE_SYSTEM_PROMPT); no anchor, check, fatal rule or threshold is reworded.
The frozen primary-study code is imported, never modified.
"""
from __future__ import annotations

import re

from jev_extension.api import jev_client as jc
from jev_extension.spec import build_spec as bs

ARMS = ("A", "B", "C", "D1", "D2")
DIMS = ("accuracy", "safety", "empathy", "completeness")
DIM_HEADINGS = {"accuracy": "### 1. CLINICAL ACCURACY (Weight: 35%)",
                "safety": "### 2. SAFETY & URGENCY (Weight: 30%)",
                "empathy": "### 3. EMPATHY & TONE (Weight: 20%)",
                "completeness": "### 4. COMPLETENESS (Weight: 15%)"}
DIM_TITLES = {"accuracy": "1. CLINICAL ACCURACY", "safety": "2. SAFETY & URGENCY",
              "empathy": "3. EMPATHY & TONE", "completeness": "4. COMPLETENESS"}
# dimension each rubric check's PENALTY line acts on (verbatim: "cap clinical_accuracy", ...)
CHECK_HEADINGS = {"endoscopic_surgery": "### Endoscopic Surgery Check",
                  "genetic_inheritance": "### Genetic Question Check",
                  "surgical_necessity": "### Surgical Necessity Check",
                  "surgical_timing": "### Timing Question Check"}
PENALTY_DIM = {"clinical_accuracy": "accuracy", "safety_urgency": "safety", "empathy_tone": "empathy",
               "completeness": "completeness"}
FATAL_HEADINGS = {"accuracy": "**Clinical Accuracy = 0 if any of:**", "safety": "**Safety & Urgency = 0 if any of:**",
                  "empathy": "**Empathy & Tone = 0 if any of:**"}


# ── verbatim rubric segments ────────────────────────────────────────────────────

def segments(spec: dict) -> dict:
    r = spec["context"]["rubric"]
    pre, rest = r.split("## SCORING DIMENSIONS (0-5 scale):", 1)
    dims_txt, rest = rest.split("## MANDATORY LOGIC CHECKS:", 1)
    checks_txt, rest = rest.split("## FATAL ERRORS (Auto 0 in relevant dimension):", 1)
    fatal_txt, closing = rest.split("Be thorough", 1)
    seg = {"preamble": pre.rstrip(), "closing": ("Be thorough" + closing).strip(), "dim_block": {}, "check_block": {},
           "check_dim": {}, "fatal_block": {}}
    heads = list(DIM_HEADINGS.items())
    for i, (d, h) in enumerate(heads):
        start = dims_txt.index(h)
        end = dims_txt.index(heads[i + 1][1]) if i + 1 < len(heads) else len(dims_txt)
        seg["dim_block"][d] = dims_txt[start:end].strip()
    ch = list(CHECK_HEADINGS.items())
    pos = sorted((checks_txt.index(h), k) for k, h in ch)
    for j, (p, k) in enumerate(pos):
        end = pos[j + 1][0] if j + 1 < len(pos) else len(checks_txt)
        block = checks_txt[p:end].strip()
        seg["check_block"][k] = block
        seg["check_dim"][k] = PENALTY_DIM[re.search(r"cap (\w+) at", block).group(1)]
    fh = list(FATAL_HEADINGS.items())
    for j, (d, h) in enumerate(fh):
        start = fatal_txt.index(h)
        end = fatal_txt.index(fh[j + 1][1]) if j + 1 < len(fh) else len(fatal_txt)
        seg["fatal_block"][d] = fatal_txt[start:end].strip()
    return seg


def anchors(spec: dict, dim: str) -> list:
    return list(next(d for d in spec["modes"]["A_matched"]["decisions"] if d["id"] == f"score_{dim}")["criteria"])


def frozen_instruction(spec: dict, dim: str) -> str:
    return next(d for d in spec["modes"]["A_matched"]["decisions"] if d["id"] == f"score_{dim}")["question"]


# ── arms ────────────────────────────────────────────────────────────────────────

def arm_a(spec, row, model) -> list:
    """Frozen original: byte-identical to the primary study's Mode A request."""
    return [jc.build_payload(spec, "A_matched", row, model)]


def arm_b(spec, row, model) -> list:
    """Minimal state: content + relevant facts in state; dimension-specific rules in the question."""
    seg = segments(spec)
    applicable = spec["applicability"]["primary_data_loader"][row["question_id"]]
    state = {"parent_question": row["question"], "ai_response": row["response"]}
    questions = {}
    for d in DIMS:
        instr = {"question": f"Which score does the AI response (`ai_response`) to the parent's question "
                             f"(`parent_question`) deserve for {DIM_TITLES[d]}? Apply the mandatory logic checks "
                             f"and fatal-error rules given here where they affect this dimension."}
        checks = [seg["check_block"][c] for c in applicable if seg["check_dim"][c] == d]
        if checks:
            instr["mandatory_logic_checks"] = checks
        if d in seg["fatal_block"]:
            instr["fatal_errors"] = seg["fatal_block"][d]
        questions[f"score_{d}"] = {"type": "score", "instructions": instr, "criteria": anchors(spec, d)}
    return [{"state": state, "model": model, "questions": questions}]


def arm_c(spec, row, model) -> list:
    """One dimension per request; frozen string-state format with other dimensions' material removed."""
    seg = segments(spec)
    case = bs.case_text(row)
    out = []
    for d in DIMS:
        parts = [seg["preamble"], "## SCORING DIMENSIONS (0-5 scale):", seg["dim_block"][d]]
        checks = [seg["check_block"][c] for c in CHECK_HEADINGS if seg["check_dim"][c] == d]
        if checks:
            parts += ["## MANDATORY LOGIC CHECKS:"] + checks
        if d in seg["fatal_block"]:
            parts += ["## FATAL ERRORS (Auto 0 in relevant dimension):", seg["fatal_block"][d]]
        parts.append(seg["closing"])
        state = "\n\n".join(parts) + "\n\n" + case
        q = {f"score_{d}": {"type": "score", "instructions": frozen_instruction(spec, d), "criteria": anchors(spec, d)}}
        out.append({"state": state, "model": model, "questions": q})
    return out


def arm_d(spec, row, model, order: str) -> list:
    """Six-level Choice: verbatim anchors as option names (description null); order asc (D1) or desc (D2)."""
    base = jc.build_payload(spec, "A_matched", row, model)
    questions = {}
    for d in DIMS:
        a = anchors(spec, d)
        seq = a if order == "asc" else a[::-1]
        questions[f"choice_{d}"] = {"type": "choice", "instructions": frozen_instruction(spec, d),
                                    "criteria": {opt: None for opt in seq}}
    return [{"state": base["state"], "model": model, "questions": questions}]


def build(arm: str, spec, row, model) -> list:
    if arm == "A":
        return arm_a(spec, row, model)
    if arm == "B":
        return arm_b(spec, row, model)
    if arm == "C":
        return arm_c(spec, row, model)
    if arm == "D1":
        return arm_d(spec, row, model, "asc")
    if arm == "D2":
        return arm_d(spec, row, model, "desc")
    raise ValueError(arm)


def option_level(spec, dim: str, option: str) -> int:
    """Choice option text -> rubric level 0-5 (by identity with the verbatim anchor)."""
    return anchors(spec, dim).index(option)
