"""
run_evaluation.py
=================
Two-phase evaluation harness for the LLM-as-a-Judge craniosynostosis study.

Phase 1  Response generation : each contestant model answers the 20 parent
                               questions using CONTESTANT_SYSTEM_PROMPT.
Phase 2  Multi-judge scoring : each judge model scores every response on the
                               four-dimension rubric using JUDGE_SYSTEM_PROMPT
                               and returns a strict JSON object.

Each evaluation is a fresh, single-response API call (no batching, no shared
context) so that judges cannot see other responses and no positional effects
arise. API keys are read from the environment (.env); no key is stored here.

This is a cleaned reference implementation of the pipeline used in the study.
It is deliberately compact — provider-specific retry/rate-limit handling is
omitted for readability.
"""

from __future__ import annotations
import os, json, csv, pathlib
from dotenv import load_dotenv
from prompts import (
    CONTESTANT_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    format_evaluation_prompt,
)

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parents[1]   # repository root
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

# --- Model registry -------------------------------------------------------
# provider identifies which client wrapper to call below.
CONTESTANTS = {
    "gpt4o":        ("openai",    "gpt-4o-2024-11-20"),
    "claude37":     ("anthropic", "claude-3-7-sonnet-20250219"),
    "llama31_70b":  ("together",  "meta-llama/Meta-Llama-3.1-70B-Instruct"),
    "gemini25_pro": ("google",    "gemini-2.5-pro-exp-03-25"),
    "deepseek_v3":  ("deepseek",  "deepseek-chat"),
}
JUDGES = {
    "opus46":            ("anthropic", "claude-opus-4-6"),
    "gpt54":             ("openai",    "gpt-5.4"),
    "gemini31":          ("google",    "gemini-3.1-pro"),
    "llama4_maverick":   ("together",  "meta-llama/Llama-4-Maverick"),
    "deepseek_reasoner": ("deepseek",  "deepseek-reasoner"),
}


# --- Thin provider wrappers ----------------------------------------------
def _chat(provider: str, model: str, system: str, user: str,
          temperature: float = 1.0) -> str:
    """Return the model's text reply for one system+user turn."""
    if provider == "openai":
        from openai import OpenAI
        c = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        r = c.chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return r.choices[0].message.content

    if provider == "deepseek":  # OpenAI-compatible endpoint
        from openai import OpenAI
        c = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                   base_url="https://api.deepseek.com")
        r = c.chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return r.choices[0].message.content

    if provider == "anthropic":
        import anthropic
        c = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        r = c.messages.create(
            model=model, max_tokens=1500, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}])
        return r.content[0].text

    if provider == "together":
        from together import Together
        c = Together(api_key=os.environ["TOGETHER_API_KEY"])
        r = c.chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return r.choices[0].message.content

    if provider == "google":
        from google import genai
        c = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        r = c.models.generate_content(
            model=model, contents=f"{system}\n\n{user}")
        return r.text

    raise ValueError(f"unknown provider: {provider}")


# --- Phase 1: response generation ----------------------------------------
def generate_responses(questions: list[dict]) -> list[dict]:
    rows = []
    for q in questions:
        for key, (prov, model) in CONTESTANTS.items():
            text = _chat(prov, model, CONTESTANT_SYSTEM_PROMPT, q["question"])
            rows.append({"question_id": q["question_id"],
                         "contestant": key, "response": text})
    (OUT / "responses.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


# --- Phase 2: multi-judge scoring ----------------------------------------
def _parse_scores(raw: str) -> dict:
    """Extract the JSON object emitted by a judge (tolerates code fences)."""
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    return json.loads(raw)


def judge_responses(questions: list[dict], responses: list[dict]) -> list[dict]:
    qmap = {q["question_id"]: q for q in questions}
    rows = []
    for r in responses:
        q = qmap[r["question_id"]]
        prompt = format_evaluation_prompt(
            question_id=q["question_id"], category=q.get("category", ""),
            question=q["question"], response=r["response"],
            complexity=q.get("complexity", "unknown"),
            emotional_weight=q.get("emotional_weight", "unknown"))
        for key, (prov, model) in JUDGES.items():
            raw = _chat(prov, model, JUDGE_SYSTEM_PROMPT, prompt)
            try:
                s = _parse_scores(raw)
            except json.JSONDecodeError:
                s = {"parse_error": True, "raw": raw}   # excluded downstream
            rows.append({"question_id": r["question_id"],
                         "contestant": r["contestant"], "judge": key, **s})
    (OUT / "judge_scores.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


if __name__ == "__main__":
    # questions.json: [{question_id, question, category, complexity, ...}, ...]
    qs = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
    resp = generate_responses(qs)
    judge_responses(qs, resp)
    print("Done. See outputs/responses.json and outputs/judge_scores.json")
