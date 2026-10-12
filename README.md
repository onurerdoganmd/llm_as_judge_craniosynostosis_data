# LLM judges in craniosynostosis patient education — data and evaluation code

Data and code accompanying:

> **Validating Large Language Model Judges for Surgical Patient Education: Multi-Expert Study of Reference
> Standard Reliability and Directional Error.** Submitted to *JMIR AI*.

Five contestant large language models (LLMs) answered 20 parent questions about craniosynostosis
(100 responses). Each response was scored on the same 4-dimension rubric by five generative LLM judges
and by three board-certified neurosurgeons. In a separately frozen extension, the same 100 responses were
evaluated by a decision-native evaluator (Jev-1.13.0, TypeSafe AI) in 10 identical runs, followed by a post
hoc, expert-blinded configuration-sensitivity analysis.

No patient data were collected. All questions were derived from publicly available medical information.

## Contents

| Path | Content |
|---|---|
| `data/questions.json` | The 20 parent questions with category, clinical complexity, expected topics, mandatory checks, and fatal-error risks |
| `data/responses.csv` | The 100 contestant responses (verbatim) |
| `data/ratings.csv` | Rubric scores of the 3 neurosurgeons and the 5 LLM judges for all 100 responses (expert reference as used in the article) |
| `data/judge_rationales.csv` | The 495 free-text rationales of the completed LLM-judge evaluations |
| `data/reference_corrections.csv` | Documented correction of AD's FAQ_07 ratings |
| `data/DATA_DICTIONARY.md` | Column definitions, reference standard, missing evaluations |
| `code/prompts.py` | Contestant system prompt, judge system prompt, and the per-response evaluation template (verbatim, as used) |
| `code/run_evaluation.py` | Two-phase harness: (1) contestant response generation, (2) blinded multi-judge scoring |
| `jev_extension/` | Frozen analysis plan, request configurations, and outputs of the decision-native evaluator extension |
| `requirements.txt` | Python dependencies of the harness |
| `.env.template` | Template for API keys (copy to `.env`; never commit it) |

## Rubric

All human and LLM evaluations used an identical rubric scored on a 0–5 integer scale:

| Dimension | Weight |
|---|---|
| Clinical accuracy | 0.35 |
| Safety and urgency | 0.30 |
| Empathy and tone | 0.20 |
| Completeness | 0.15 |

Weighted total = 0.35 × Accuracy + 0.30 × Safety + 0.20 × Empathy + 0.15 × Completeness. The prespecified
clinical adequacy threshold is ≥4.0. The expert reference standard is the mean of the three neurosurgeons.

## Reproducing response generation and judging

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.template .env              # then add your own API keys
python code/run_evaluation.py      # reads data/questions.json, writes outputs/
```

Each evaluation is a single, fresh API call containing only the rubric, the question, and one response;
no batch or multi-response requests were used. Model identifiers reflect the versions accessed in January
2026. Provider APIs and model names change over time, so re-running the harness will not reproduce the
original outputs exactly; the original outputs are provided in `data/`.

## Scope

- **Statistical analysis.** The statistical analysis script is not part of this release. All methods are
  specified in the Methods section of the article: ICC(2,1)/ICC(2,k) (two-way random-effects, absolute
  agreement), quadratic-weighted Gwet AC2, Krippendorff α, PABAK on 3 clinical tiers, Prevalence Index,
  PA±0/±1, Wilcoxon signed-rank and Bland-Altman analysis, underscoring/overscoring ratios, gatekeeper
  ROC-AUC and zero-miss thresholds, and the Self-Bias Index with bootstrap CIs (10,000 iterations, seed 42),
  permutation tests, and Benjamini-Hochberg correction. Analyses were run in Python (≥3.9) with pandas,
  numpy, scipy, pingouin, and statsmodels.
- **Rationale-based safety coding** was performed manually by a single analyst using the coding schema in
  Multimedia Appendix 1 of the article. It is not automated here.
- **Jev extension.** Jev was accessed through the vendor's paid commercial API at the authors' expense; the
  vendor had no role in the study. See `jev_extension/README.md`.

## Citation

If you use these materials, please cite the article (see `CITATION.cff`).

## License

Code: MIT License (`LICENSE`). Data (`data/`, `jev_extension/outputs/`): Creative Commons Attribution 4.0
International (CC BY 4.0), see `data/LICENSE.md`.
