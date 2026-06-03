"""Standalone CLI tool for adversarial evaluation of disambiguation results."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import csv
import logging
import os
import random
import sys
from datetime import datetime
from pathlib import Path

import click
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import UsageLimits

from models import CriticResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
DEFAULT_MODEL = "z-ai/glm-4.6"

# Common first names for suspect heuristic
COMMON_FIRST_NAMES = {
    "james",
    "john",
    "robert",
    "michael",
    "david",
    "william",
    "richard",
    "joseph",
    "thomas",
    "charles",
    "christopher",
    "daniel",
    "matthew",
    "anthony",
    "mark",
    "donald",
    "steven",
    "paul",
    "andrew",
    "joshua",
    "mary",
    "patricia",
    "jennifer",
    "linda",
    "barbara",
    "elizabeth",
    "susan",
    "jessica",
    "sarah",
    "karen",
    "lisa",
    "nancy",
    "betty",
    "margaret",
    "sandra",
    "ashley",
    "dorothy",
    "kimberly",
    "emily",
    "donna",
    "maria",
}

COMMON_LAST_NAMES = {
    "smith",
    "johnson",
    "williams",
    "brown",
    "jones",
    "garcia",
    "miller",
    "davis",
    "rodriguez",
    "martinez",
    "hernandez",
    "lopez",
    "gonzalez",
    "wilson",
    "anderson",
    "thomas",
    "taylor",
    "moore",
    "jackson",
    "martin",
    "lee",
    "thompson",
    "white",
    "harris",
    "clark",
    "lewis",
    "robinson",
    "walker",
    "young",
    "allen",
    "king",
    "wright",
    "scott",
    "hill",
    "green",
    "adams",
    "baker",
    "hall",
    "nelson",
    "carter",
}

CRITERION_NAMES = [
    "Evidence Relevance Filter",
    "Name Commonality Calibration",
    "Search Strategy Completeness",
    "Evidence Tier Classification",
    "Corroboration Requirement",
    "Full Name Verification",
    "Evidence URL Quality",
    "Reasoning Coherence",
    "Confidence Calibration",
    "Known Pitfall Check",
]

SYSTEM_PROMPT = """\
You are an adversarial quality reviewer for superintendent disambiguation results.
Your job is to find flaws in the disambiguator's reasoning — cases where the prediction
may be wrong, the confidence is miscalibrated, or the reasoning has logical gaps.

You are READ-ONLY. You evaluate and score. You do not fix.

## The 10-Point Audit Checklist

Score each criterion PASS / WEAK / FAIL.

### 1. Evidence Relevance Filter
- Did the reasoning dismiss non-US and non-K-12 results?
- FAIL if reasoning cites non-US or non-K-12 evidence as supporting a conclusion.

### 2. Name Commonality Calibration
- For VERY COMMON names (John Smith, Michael Johnson, etc.): Were 2+ Tier 1-2 sources required?
- For UNCOMMON names: Was a single strong source accepted appropriately?
- FAIL if common name predicted "same" based only on timeline plausibility.

### 3. Search Strategy Completeness
- Were all 4 phases attempted or correctly short-circuited?
  Phase 1: Background (district domains), Phase 2: Direct connection (include_domains),
  Phase 3: Individual verification (full name at both districts),
  Phase 4: Archive deep-dive (for records 5+ years old).
- FAIL if only 1-2 phases attempted before concluding.

### 4. Evidence Tier Classification
- Tier 1: LinkedIn career history, hiring announcement mentioning prior district, transition news.
- Tier 2: Obituary, education publication, archived staff pages.
- Tier 3: District bio (current only), single-district news.
- Tier 4: Timeline plausibility, geographic proximity.
- FAIL if Tier 3-4 evidence treated as definitive.

### 5. Corroboration Requirement
- 2+ independent sources pointing the same direction?
- High confidence "same": 2+ Tier 1-2 sources?
- High confidence "different": temporal impossibility OR 2+ sources showing distinct people?
- FAIL if single source used for high confidence conclusion.

### 6. Full Name Verification
- Was FULL NAME (first + last) verified at BOTH districts?
- FAIL if reasoning relies on last name match without verifying first name.

### 7. Evidence URL Quality
- URLs exist and seem relevant? Enough to support the claim?
- FAIL if no URLs or URLs clearly about wrong person/topic.

### 8. Reasoning Coherence
- Does reasoning logically lead to the prediction? Internal contradictions?
- FAIL if reasoning says "limited evidence" but conclusion is "high confidence".

### 9. Confidence Calibration
- High: 2+ Tier 1-2 independent sources, no contradictions.
- Medium: 1 Tier 1 OR 2+ Tier 2-3 sources, no contradictions.
- Low: Circumstantial evidence only, some uncertainty.
- FAIL if confidence too high for evidence (overconfident).

### 10. Known Pitfall Check
- Absence of evidence treated as evidence of absence?
- Geographic distance used as evidence of different people?
- Career progression misread? Charter networks missed? Snippets without fetching?
- FAIL if any known pitfall is present.

## Scoring
PASS = 2 pts, WEAK = 1 pt, FAIL = 0 pts. Total out of 20.

## Grades
- CONFIRMED: 18-20. Sound reasoning, strong evidence, prediction almost certainly correct.
- PLAUSIBLE: 14-17. Minor gaps but conclusion probably right.
- QUESTIONABLE: 10-13. Significant gaps, prediction might be wrong.
- SUSPECT: 0-9. Major flaws, prediction likely wrong or unsupported.

## Rules
- Be ADVERSARIAL. Find problems. Do not rubber-stamp.
- "same" with weak evidence is MORE dangerous than "different" with weak evidence.
- Pay extra attention to common names — highest-risk cases.
- If reasoning is excellent, say so.

## Output
Return exactly 10 criteria (in order), a key_concern, whether a rerun would help, and why.\
"""


def _is_funds_exhausted(error_message: str) -> bool:
    """Detect OpenRouter credit exhaustion from an error message."""
    msg = error_message.lower()
    if "payment required" in msg:
        return True
    if "insufficient" in msg and "credit" in msg:
        return True
    if "out of credits" in msg or "credit balance" in msg:
        return True
    if "openrouter" in msg and "402" in msg:
        return True
    if "openrouter" in msg and "credit" in msg:
        return True
    return False


def _is_common_name(name: str) -> bool:
    """Check if a name is common (both first and last name are common)."""
    parts = name.lower().split()
    if len(parts) < 2:
        return False
    return parts[0] in COMMON_FIRST_NAMES and parts[-1] in COMMON_LAST_NAMES


def is_suspect(row: dict) -> bool:
    """Heuristic to flag high-risk cases that deserve extra scrutiny."""
    name = row.get("name", "")
    prediction = row.get("prediction", "")
    confidence = row.get("confidence", "")
    reasoning = row.get("reasoning", "")
    urls = row.get("evidence_urls", "")

    # Common name predicted "same"
    if _is_common_name(name) and prediction == "same":
        return True

    # Uncertain prediction
    if prediction == "uncertain":
        return True

    # No evidence URLs
    if not urls or not urls.strip():
        return True

    # Short reasoning (< 200 chars)
    if len(reasoning) < 200:
        return True

    # High-confidence "same" with very few URLs
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if prediction == "same" and confidence == "high" and len(url_list) <= 1:
        return True

    return False


def load_results_csv(path: Path) -> list[dict]:
    """Load disambiguation results from a CSV, skipping error rows."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("prediction", "").lower() == "error":
                continue
            rows.append(row)
    return rows


def filter_rows(
    rows: list[dict],
    *,
    sample: int | None = None,
    suspect_only: bool = False,
    matches_only: bool = False,
    case_name: str | None = None,
) -> list[dict]:
    """Apply filtering to result rows."""
    filtered = rows

    if matches_only:
        filtered = [
            r
            for r in filtered
            if r.get("prediction", "").lower() == "same"
            and r.get("confidence", "").lower() == "high"
        ]

    if case_name:
        needle = case_name.lower()
        filtered = [r for r in filtered if needle in r.get("name", "").lower()]

    if suspect_only:
        filtered = [r for r in filtered if is_suspect(r)]

    if sample is not None and sample < len(filtered):
        filtered = random.sample(filtered, sample)

    return filtered


def build_case_prompt(row: dict) -> str:
    """Build the evaluation prompt for a single case."""
    name = row.get("name", "unknown")
    d1 = row.get("leaid_name1", "?")
    s1 = row.get("state1", "?")
    y1 = row.get("year1", "?")
    d2 = row.get("leaid_name2", "?")
    s2 = row.get("state2", "?")
    y2 = row.get("year2", "?")
    prediction = row.get("prediction", "?")
    confidence = row.get("confidence", "?")
    reasoning = row.get("reasoning", "")
    urls = row.get("evidence_urls", "")

    lines = [
        "Evaluate this disambiguation result:\n",
        f"Name: {name}",
        f"Position 1: {d1} ({s1}), {y1}",
        f"Position 2: {d2} ({s2}), {y2}",
    ]

    # Optional position 3
    d3 = row.get("leaid_name3", "")
    s3 = row.get("state3", "")
    y3 = row.get("year3", "")
    if d3 and s3 and y3:
        lines.append(f"Position 3: {d3} ({s3}), {y3}")

    lines.extend(
        [
            f"\nPrediction: {prediction}",
            f"Confidence: {confidence}",
            f"\nReasoning:\n{reasoning}",
            f"\nEvidence URLs:\n{urls if urls else '(none)'}",
        ]
    )

    return "\n".join(lines)


def create_critic_agent(model_id: str) -> Agent[None, CriticResult]:
    """Create the critic agent with the given model."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise click.ClickException("OPENROUTER_API_KEY environment variable is required")

    model = OpenRouterModel(
        model_id,
        provider=OpenRouterProvider(api_key=api_key),
    )

    agent: Agent[None, CriticResult] = Agent(
        model=model,
        output_type=CriticResult,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
    )

    return agent


async def evaluate_case(
    agent: Agent[None, CriticResult],
    row: dict,
) -> CriticResult | None:
    """Evaluate a single case with retry logic. Returns None on failure."""
    prompt = build_case_prompt(row)
    backoff = INITIAL_BACKOFF

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = await agent.run(
                prompt,
                usage_limits=UsageLimits(request_limit=5),
            )
            return result.output
        except Exception as e:
            error_msg = str(e)
            logger.warning(
                f"Attempt {attempt}/{MAX_RETRIES} failed for {row.get('name', '?')}: {e}"
            )
            if _is_funds_exhausted(error_msg):
                raise
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= 2

    return None


def _grade_symbol(grade: str) -> str:
    """Return a text indicator for a grade."""
    return {
        "CONFIRMED": "[OK]",
        "PLAUSIBLE": "[~~]",
        "QUESTIONABLE": "[??]",
        "SUSPECT": "[!!]",
    }.get(grade, "[--]")


def write_companion_csv(
    path: Path,
    evaluated: list[tuple[dict, CriticResult | None]],
) -> None:
    """Write the companion CSV with per-case scores."""
    fieldnames = (
        [
            "name",
            "prediction",
            "confidence",
            "critic_score",
            "critic_grade",
        ]
        + [f"c{i}" for i in range(1, 11)]
        + [
            "key_concern",
            "would_rerun_help",
        ]
    )

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row, result in evaluated:
            out: dict = {
                "name": row.get("name", ""),
                "prediction": row.get("prediction", ""),
                "confidence": row.get("confidence", ""),
            }
            if result:
                out["critic_score"] = result.total_score
                out["critic_grade"] = result.grade
                for i, c in enumerate(result.criteria[:10], 1):
                    out[f"c{i}"] = c.score
                out["key_concern"] = result.key_concern
                out["would_rerun_help"] = result.would_rerun_help
            else:
                out["critic_score"] = "error"
                out["critic_grade"] = "error"
            writer.writerow(out)


def write_passed_csv(
    path: Path,
    evaluated: list[tuple[dict, CriticResult | None]],
    min_score: int = 18,
) -> None:
    """Write a CSV containing only CONFIRMED cases (score >= 18).

    Preserves all original row columns and appends critic_score and critic_grade.
    """
    passed = [(row, cr) for row, cr in evaluated if cr is not None and cr.total_score >= min_score]
    if not passed:
        logger.info(f"Passed CSV: no cases scored >= {min_score}, skipping")
        return

    # Fieldnames: all original columns from the input row, plus critic columns
    original_fields = list(passed[0][0].keys())
    fieldnames = original_fields + ["critic_score", "critic_grade"]

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row, cr in passed:
            out = dict(row)
            out["critic_score"] = cr.total_score
            out["critic_grade"] = cr.grade
            writer.writerow(out)


def write_report(
    path: Path,
    evaluated: list[tuple[dict, CriticResult | None]],
) -> None:
    """Write the markdown report."""
    now = datetime.now()
    successful = [(r, cr) for r, cr in evaluated if cr is not None]
    failed = [(r, cr) for r, cr in evaluated if cr is None]

    # Grade distribution
    grade_counts: dict[str, int] = {"CONFIRMED": 0, "PLAUSIBLE": 0, "QUESTIONABLE": 0, "SUSPECT": 0}
    for _, cr in successful:
        grade_counts[cr.grade] = grade_counts.get(cr.grade, 0) + 1

    rerun_candidates = [(r, cr) for r, cr in successful if cr.would_rerun_help]

    lines: list[str] = []
    lines.append("# Disambiguation Critic Report")
    lines.append(f"Date: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Cases Evaluated: {len(evaluated)} ({len(failed)} errors)\n")

    lines.append("## Grade Distribution\n")
    total = len(successful) or 1
    for grade in ["CONFIRMED", "PLAUSIBLE", "QUESTIONABLE", "SUSPECT"]:
        count = grade_counts[grade]
        pct = count / total * 100
        lines.append(f"- **{grade}**: {count} cases ({pct:.0f}%)")

    lines.append("")

    # Per-case details
    lines.append("## Per-Case Results\n")
    for row, cr in successful:
        name = row.get("name", "?")
        d1 = row.get("leaid_name1", "?")
        d2 = row.get("leaid_name2", "?")
        pred = row.get("prediction", "?")
        conf = row.get("confidence", "?")

        lines.append(f"### {name} — {d1} -> {d2}")
        lines.append(f"**Prediction:** {pred} ({conf})")
        lines.append(f"**Critic Score:** {cr.total_score}/20 — {cr.grade}\n")

        lines.append("| # | Criterion | Score | Notes |")
        lines.append("|---|-----------|-------|-------|")
        for i, c in enumerate(cr.criteria, 1):
            lines.append(f"| {i} | {c.name} | {c.score.upper()} | {c.notes} |")
        lines.append("")
        lines.append(f"**Key Concern:** {cr.key_concern}")
        rerun_str = "YES" if cr.would_rerun_help else "NO"
        lines.append(f"**Would Re-Run Help?** {rerun_str} — {cr.rerun_reason}\n")

    # Systematic issues — look for criteria that frequently fail
    if successful:
        lines.append("## Systematic Issues\n")
        fail_counts: dict[str, int] = {}
        for _, cr in successful:
            for c in cr.criteria:
                if c.score == "fail":
                    fail_counts[c.name] = fail_counts.get(c.name, 0) + 1
        if fail_counts:
            for cname, count in sorted(fail_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- **{cname}**: failed in {count}/{len(successful)} cases")
        else:
            lines.append("- No systematic failures detected.")
        lines.append("")

    # Re-run recommendations
    if rerun_candidates:
        lines.append("## Recommended Re-Runs\n")
        for row, cr in rerun_candidates:
            lines.append(f"- **{row.get('name', '?')}** — {cr.rerun_reason}")
        lines.append("")

    # Failed evaluations
    if failed:
        lines.append("## Failed Evaluations\n")
        for row, _ in failed:
            lines.append(f"- {row.get('name', '?')}: critic evaluation failed")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


async def run_critic_on_rows(
    rows: list[dict],
    model_id: str = DEFAULT_MODEL,
    matches_only: bool = True,
) -> list[tuple[dict, CriticResult | None]]:
    """Evaluate rows in-memory. Returns list of (row, CriticResult|None) tuples.

    This is the programmatic entry point used by process_with_retry.py.
    Rows are filtered (matches_only) but not loaded from disk.
    """
    filtered = filter_rows(rows, matches_only=matches_only)
    logger.info(f"Critic: evaluating {len(filtered)} cases after filtering")

    if not filtered:
        logger.info("Critic: no cases match the filter criteria")
        return []

    agent = create_critic_agent(model_id)
    logger.info(f"Critic: using model {model_id}")

    evaluated: list[tuple[dict, CriticResult | None]] = []

    async with agent:
        for idx, row in enumerate(filtered):
            name = row.get("name", "?")
            pred = row.get("prediction", "?")
            logger.info(f"Critic: {idx + 1}/{len(filtered)}: {name} ({pred})")

            try:
                result = await evaluate_case(agent, row)
            except Exception as e:
                if _is_funds_exhausted(str(e)):
                    logger.error(f"Critic: funds exhausted: {e}")
                    evaluated.append((row, None))
                    for rem_row in filtered[idx + 1 :]:
                        evaluated.append((rem_row, None))
                    break
                logger.error(f"Critic: unexpected error for {name}: {e}")
                result = None

            evaluated.append((row, result))

            if result:
                sym = _grade_symbol(result.grade)
                logger.info(
                    f"  {sym} {name}: {result.total_score}/20 {result.grade} "
                    f"| {result.key_concern[:60]}"
                )

    return evaluated


async def run_critic(
    input_path: Path,
    output_path: Path | None,
    model_id: str,
    sample: int | None,
    suspect_only: bool,
    matches_only: bool,
    case_name: str | None,
) -> None:
    """CLI async entry point — loads from file, writes reports."""
    rows = load_results_csv(input_path)
    logger.info(f"Loaded {len(rows)} result rows from {input_path}")

    if not rows:
        logger.error("No valid result rows found")
        return

    filtered = filter_rows(
        rows,
        sample=sample,
        suspect_only=suspect_only,
        matches_only=matches_only,
        case_name=case_name,
    )
    logger.info(f"Evaluating {len(filtered)} cases after filtering")

    if not filtered:
        logger.error("No cases match the filter criteria")
        return

    # Determine output paths
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    report_dir = Path("quality_reports")
    report_dir.mkdir(exist_ok=True)

    if output_path:
        report_path = output_path
    else:
        report_path = report_dir / f"critic_{timestamp}.md"

    csv_path = report_path.with_suffix(".csv")

    agent = create_critic_agent(model_id)
    logger.info(f"Using model: {model_id}")

    evaluated: list[tuple[dict, CriticResult | None]] = []

    try:
        async with agent:
            for idx, row in enumerate(filtered):
                name = row.get("name", "?")
                pred = row.get("prediction", "?")
                logger.info(f"Evaluating {idx + 1}/{len(filtered)}: {name} ({pred})")

                try:
                    result = await evaluate_case(agent, row)
                except Exception as e:
                    if _is_funds_exhausted(str(e)):
                        logger.error(f"Funds exhausted: {e}")
                        evaluated.append((row, None))
                        for rem_row in filtered[idx + 1 :]:
                            evaluated.append((rem_row, None))
                        break
                    logger.error(f"Unexpected error for {name}: {e}")
                    result = None

                evaluated.append((row, result))

                if result:
                    sym = _grade_symbol(result.grade)
                    click.echo(
                        f"  {sym} {name}: {result.total_score}/20 {result.grade} "
                        f"| {result.key_concern[:60]}"
                    )
                else:
                    click.echo(f"  [ERR] {name}: evaluation failed")

    except KeyboardInterrupt:
        logger.info("Interrupted — writing partial report")

    # Write outputs
    if evaluated:
        write_report(report_path, evaluated)
        write_companion_csv(csv_path, evaluated)
        passed_path = report_path.with_name(report_path.stem + "_passed.csv")
        write_passed_csv(passed_path, evaluated)
        logger.info(f"Report: {report_path}")
        logger.info(f"CSV:    {csv_path}")
        passed_count = sum(1 for _, cr in evaluated if cr is not None and cr.total_score >= 14)
        logger.info(f"Passed CSV: {passed_path} ({passed_count} cases)")
    else:
        logger.warning("No cases evaluated — no report written")


@click.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Output report path (default: quality_reports/critic_TIMESTAMP.md)",
)
@click.option("-m", "--model", "model_id", default=DEFAULT_MODEL, help="OpenRouter model ID")
@click.option("--sample", type=int, default=None, help="Randomly sample N cases")
@click.option("--suspect-only", is_flag=True, help="Only evaluate high-risk cases")
@click.option(
    "--matches-only",
    is_flag=True,
    help="Only evaluate high-confidence 'same' predictions",
)
@click.option("--case", "case_name", default=None, help="Filter to cases matching this name")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def main(
    input_file: Path,
    output_path: Path | None,
    model_id: str,
    sample: int | None,
    suspect_only: bool,
    matches_only: bool,
    case_name: str | None,
    verbose: bool,
) -> None:
    """Adversarial evaluation of disambiguation result CSVs.

    Reads a disambiguation output CSV and uses an LLM to audit each case's
    reasoning quality against a 10-point checklist.

    \b
    Examples:
        uv run python critic.py results.csv
        uv run python critic.py results.csv --matches-only
        uv run python critic.py results.csv --sample 20
        uv run python critic.py results.csv --suspect-only
        uv run python critic.py results.csv --case "John Smith"
        uv run python critic.py results.csv -o report.md
        uv run python critic.py results.csv -m anthropic/claude-3.5-haiku
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        asyncio.run(
            run_critic(
                input_file, output_path, model_id, sample, suspect_only, matches_only, case_name
            )
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
