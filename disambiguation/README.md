# Superintendent Disambiguation Tool

Determines whether two superintendent records with the same name represent the same person or different people. Uses an LLM agent with web search tools (Tavily, Wayback Machine) to find evidence connecting superintendent tenures across districts, then applies an adversarial critic to validate the results.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run:
   ```bash
   uv sync
   ```

2. Copy `.env.example` to `.env` and fill in your API keys:
   ```bash
   cp .env.example .env
   ```
   - **OPENROUTER_API_KEY**: LLM provider ([openrouter.ai](https://openrouter.ai))
   - **TAVILY_API_KEY**: Web search provider ([tavily.com](https://tavily.com))

## Usage

### Single case test
```bash
uv run python main.py test --name "John Smith" \
    --district1 "Springfield SD" --state1 IL --year1 2018 \
    --district2 "Lincoln SD" --state2 MO --year2 2021
```

### Batch processing
```bash
uv run python main.py process input.csv output.csv
```

### Unified pipeline (process + retry errors + critic)
```bash
uv run python process_with_retry.py input.csv output.csv
```

### Standalone critic evaluation
```bash
uv run python critic.py output.csv --matches-only
```

## Output Schema

Every output row contains:
- `prediction`: "same", "different", or "uncertain"
- `confidence`: "high", "medium", or "low"
- `reasoning`: Multi-sentence explanation with evidence tier classifications
- `evidence_urls`: Comma-separated URLs supporting the determination

When the critic is run (`process_with_retry.py` or `critic.py --matches-only`), two additional columns are appended to high-confidence "same" rows:
- `critic_score`: 0-20 (sum of 10 criteria, each scored pass=2/weak=1/fail=0)
- `critic_grade`: CONFIRMED (18-20), PLAUSIBLE (14-17), QUESTIONABLE (10-13), SUSPECT (0-9)

## Match Decision Rule

A case is a confirmed match **only** if all three conditions are met:
1. `prediction` = "same"
2. `confidence` = "high"
3. `critic_score` >= 18 (grade = CONFIRMED)

Everything else is treated as a non-match.

## Production Run (1,512 cross-state cases)

- **Input**: `output/cross_state_matches_wide.csv` (1,512 cases)
- **Results**: `output/cross_state_matches_output_merged.csv` (all predictions)
- **Confirmed matches**: `output/critic_2026-03-05_084008_passed.csv` (264 cases)
- **Critic audit**: `output/critic_2026-03-05_084008.md` (full report)
- **Validation**: `output/ground_truth_200_manual.csv` (200-case manual review)

### Summary

| Metric | Value |
|-|-|
| Total cases | 1,512 |
| Different | 930 |
| Same | 545 (525 high-confidence) |
| Uncertain | 37 |
| Critic evaluated | 411 high-confidence "same" |
| **Confirmed matches** | **264** |

Test set accuracy (200 cases): 99.0% accuracy, 97.4% precision, 100.0% recall.
