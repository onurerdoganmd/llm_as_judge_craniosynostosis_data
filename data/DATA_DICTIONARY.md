# Data dictionary

All files are UTF-8. `response_id` (`<question_id>_<contestant_model_key>`, e.g. `FAQ_05_gpt4o`) links the files.

## `questions.json`

20 objects, one per parent question.

| Field | Description |
|---|---|
| `question_id` | `FAQ_01` … `FAQ_20` |
| `category` | `diagnosis_causes` (5), `treatment_decisions` (5), `surgical_risks` (4), `recovery_care` (3), `longterm_outcomes` (3) |
| `question` | Question text as submitted to the contestant models |
| `parent_concern_level` | Anticipated parental concern (4–5) |
| `clinical_complexity` | `low`, `medium`, `high` |
| `emotional_weight` | `medium`, `high`, `very_high` |
| `source_prevalence` | Share of the reviewed medical websites covering the topic |
| `expected_topics` | Clinical facts an adequate response is expected to cover |
| `mandatory_checks` | Rubric mandatory logic checks triggered by the question: `endoscopic_surgery`, `genetic_inheritance`, `surgical_necessity`, `surgical_timing` |
| `fatal_error_risks` | Fatal-error types monitored for the question: `home_remedy_suggested`, `dismissing_diagnosis`, `risks_minimized`, `parent_blaming`, `contraindication_unjustified` |

## `responses.csv`

100 rows (20 questions × 5 contestant models), collected on January 11, 2026.

| Column | Description |
|---|---|
| `eval_no` | Response number (1–100) |
| `response_id`, `question_id` | Identifiers |
| `contestant_model`, `contestant_model_key` | Contestant label and key (`gpt4o`, `claude`, `llama`, `gemini`, `deepseek`) |
| `model_id` | Model identifier returned by the provider API at generation time |
| `timestamp` | Generation time (local time) |
| `response` | Verbatim response text |

## `ratings.csv`

100 rows. Scores are on the 0–5 rubric scale; `<rater>_overall` = 0.35 × accuracy + 0.30 × safety +
0.20 × empathy + 0.15 × completeness.

| Column group | Rater |
|---|---|
| `opus46_*` | Opus-4.6 (Anthropic), LLM judge; own-family contestant `claude` |
| `gpt54_*` | GPT-5.4 (OpenAI), LLM judge; own-family contestant `gpt4o` |
| `gemini31_*` | Gemini-3.1 (Google DeepMind), LLM judge; own-family contestant `gemini` |
| `llama4_maverick_*` | Llama-4 Maverick (Meta), LLM judge; own-family contestant `llama` |
| `deepseek_reasoner_*` | DeepSeek Reasoner (DeepSeek), LLM judge; own-family contestant `deepseek` |
| `human_ad_*`, `human_ms_*`, `human_ys_*` | Board-certified neurosurgeons AD, MS, and YS (co-authors; AD and YS from institution A, MS from institution B) |

Each group has `_accuracy`, `_safety`, `_empathy`, `_completeness`, `_overall`.

**Expert reference standard.** The mean of the available expert ratings for each response and dimension
(overall: mean of the experts' weighted overall scores). A response is *inadequate* when the consensus
overall score is <4.0 (10 of 100) and *safety-inadequate* when the consensus Safety score is <4.0 (5 of 100).

**Missing values (left empty; not imputed).**

| Rater | Missing response(s) |
|---|---|
| Opus-4.6 | `FAQ_08_deepseek`, `FAQ_10_claude`, `FAQ_10_deepseek`, `FAQ_19_gemini` |
| DeepSeek Reasoner | `FAQ_10_gemini` |
| MS | `FAQ_05_gpt4o` (consensus based on AD and YS) |

**Reference correction.** AD's ratings for the five FAQ_07 responses are the author-supplied original
ratings that resolved an expert-response matching discrepancy; see `reference_corrections.csv`. All other
ratings are unchanged from the original rating files.

## `judge_rationales.csv`

495 rows: one free-text rationale per completed LLM-judge evaluation (`response_id`, `question_id`,
`contestant_model_key`, `judge`, `rationale`). The rationales were the basis of the manual, single-analyst
safety-concern coding described in the article. The `overall` value in `ratings.csv` is recomputed from the
four dimension scores with the study weights; it may differ slightly from an overall value a judge
wrote in its own output.

## `reference_corrections.csv`

The five corrected AD ratings for FAQ_07 with source, date, and reason.
