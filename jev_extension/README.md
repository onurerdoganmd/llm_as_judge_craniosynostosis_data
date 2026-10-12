# Decision-native evaluator extension (Jev-1.13.0)

The identical frozen set of 100 contestant responses and the same clinical rubric were evaluated by Jev
(TypeSafe AI), pinned to version 1.13.0. Jev was accessed through the vendor's paid commercial API at the
authors' expense; the vendor had no role in study design, analysis, interpretation, or reporting.

## Freeze records

| File | Content |
|---|---|
| `spec/jev_spec_v1.json` | Evaluation specification: decision wording, Score anchors, typed decisions, state construction |
| `spec/FROZEN.json` | Freeze record of the specification (UTC time, SHA-256 of the spec and of the decision wording) |
| `config/frozen_config.yaml` | Frozen study configuration: rubric weights, thresholds, reference-standard definitions, error classification |
| `config/frozen_original_inputs.json` | SHA-256 of the original benchmark inputs (responses, ratings, rubric) |
| `config/PHASE5_FREEZE.json` | Freeze record of the primary analysis, made before the full benchmark was run |
| `config/CS_FREEZE.json` | Freeze record of the configuration-sensitivity design (configurations A–D2) |
| `config/CS_EFFECT_FREEZE.json` | Freeze record of the expert-blinded configuration-effect report, made before comparison with the expert reference |
| `config/config_sensitivity_payload_manifest.json` | Request payload definitions for configurations A–D2 |
| `manifests/` | Per-run manifests (UTC timestamps, file hashes) for the pilot, the 10 primary runs, and the 15 configuration-sensitivity runs |

## Outputs

| File | Content |
|---|---|
| `outputs/primary_matched_scores_long.csv` | Frozen configuration A, matched mode: 10 runs × 100 responses × 4 dimensions; Score decision (`score`), `confidence`, and probability distribution over the 0–5 anchors (`p0`–`p5`) |
| `outputs/primary_native_decisions_long.csv` | Frozen configuration A, typed mode: 10 runs × 100 responses; probability of "yes" (`p_yes`) for overall inadequacy, safety inadequacy, fatal-error items, and mandatory-check requirements |
| `outputs/config_sensitivity_run_level.csv` | Configurations A–D2, 3 runs each: per-run, per-response, per-dimension expected score, modal score, and probabilities |
| `outputs/config_sensitivity_response_means.csv` | Configuration-sensitivity results averaged within response (unit of analysis) |
| `outputs/config_effect_results.json` | Expert-blinded configuration-effect estimates (question-clustered bootstrap, 5000 replicates) |
| `outputs/raw_primary_runs.zip` | Complete request/response records of the 2000 primary API calls (10 runs × 100 responses × 2 modes) |
| `outputs/raw_config_sensitivity_runs.zip` | Complete request/response records of the configuration-sensitivity runs |

Each raw record contains the exact request body, its SHA-256, timestamps, HTTP status, provider request ID,
and the returned body. No API credentials are stored.

## Code

`code/` contains the API client (`jev_client.py`), the primary run script (`run_jev.py`), and the request
builders and runner for configurations A–D2. These files document exactly how requests were constructed;
they depend on internal helper modules of the study repository and are not intended as a standalone package.
A key is read from the `TYPESAFE_API_KEY` environment variable and is never written to disk.

Configurations: **A** frozen primary (information parity with the generative-judge rubric); **B** minimal
state; **C** one dimension per request; **D1/D2** Choice formulation with ascending/descending option order.
