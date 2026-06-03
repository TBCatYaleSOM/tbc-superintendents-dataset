"""Unified pipeline: disambiguate, retry errors, run critic, output single CSV."""

from __future__ import annotations

import asyncio
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import click

from critic import DEFAULT_MODEL
from retry_errors import extract_errors, merge_results, row_key

if TYPE_CHECKING:
    from models import CriticResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Result CSV headers (duplicated from main.py to avoid heavy import at module level)
RESULT_HEADERS = [
    "name",
    "year1",
    "state1",
    "leaid1",
    "leaid_name1",
    "year2",
    "state2",
    "leaid2",
    "leaid_name2",
    "year3",
    "state3",
    "leaid3",
    "leaid_name3",
    "prediction",
    "confidence",
    "reasoning",
    "evidence_urls",
]

# Extended headers: original + critic columns
UNIFIED_HEADERS = RESULT_HEADERS + ["critic_score", "critic_grade"]


def _count_errors(csv_path: Path) -> int:
    """Count error rows in a CSV file."""
    count = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("prediction", "").strip().lower() == "error":
                count += 1
    return count


def _read_all_rows(csv_path: Path) -> list[dict]:
    """Read all rows from a CSV file."""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_unified_csv(
    output_path: Path,
    all_rows: list[dict],
    critic_results: dict[tuple, CriticResult],
) -> None:
    """Write the final unified CSV — all rows with critic columns on matches."""
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=UNIFIED_HEADERS)
        writer.writeheader()
        for row in all_rows:
            out = {h: row.get(h, "") for h in RESULT_HEADERS}
            key = row_key(row)
            cr = critic_results.get(key)
            if cr:
                out["critic_score"] = cr.total_score
                out["critic_grade"] = cr.grade
            else:
                out["critic_score"] = ""
                out["critic_grade"] = ""
            writer.writerow(out)


async def run_pipeline(
    input_path: Path,
    output_path: Path,
    model: str,
    max_retries: int,
    unique_districts: int | None,
    no_critic: bool,
) -> None:
    """Full pipeline: disambiguate -> retry errors -> critic -> unified output."""
    # Lazy imports — these pull in curl_cffi, Tavily, pydantic-ai providers, etc.
    from dotenv import load_dotenv

    load_dotenv()

    from content_store import get_store
    from critic import run_critic_on_rows, write_companion_csv, write_passed_csv, write_report
    from disambiguator import create_agent
    from main import FundsExhausted, _case_to_row_dict, load_cases, process_case_with_retry

    # --- Step 1: Initial disambiguation ---
    logger.info("=" * 60)
    logger.info("STEP 1: Initial disambiguation")
    logger.info("=" * 60)

    # Use a temp file for the initial run, final output gets critic columns
    work_dir = output_path.parent
    stem = output_path.stem
    initial_csv = work_dir / f"{stem}_initial.csv"

    cases = load_cases(input_path, unique_districts)
    logger.info(f"Loaded {len(cases)} cases from {input_path}")

    if not cases:
        logger.error("No cases found in input file")
        return

    # Write header
    with open(initial_csv, "w", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=RESULT_HEADERS).writeheader()

    agent = create_agent(model)
    funds_exhausted = False

    async with agent:
        for idx, case in enumerate(cases):
            districts = f"{case.position1.district_name} -> {case.position2.district_name}"
            if case.position3:
                districts += f" -> {case.position3.district_name}"
            logger.info(f"Processing {idx + 1}/{len(cases)}: {case.name} ({districts})")

            try:
                result, error_msg = await process_case_with_retry(agent, case)
            except FundsExhausted as e:
                error_msg = f"FundsExhausted: {str(e)[:120]}"
                logger.error(error_msg)
                # Write this case + all remaining as errors
                row = _case_to_row_dict(case, None, error_msg)
                with open(initial_csv, "a", encoding="utf-8", newline="") as f:
                    csv.DictWriter(f, fieldnames=RESULT_HEADERS).writerow(row)
                for rem_case in cases[idx + 1 :]:
                    row = _case_to_row_dict(rem_case, None, error_msg)
                    with open(initial_csv, "a", encoding="utf-8", newline="") as f:
                        csv.DictWriter(f, fieldnames=RESULT_HEADERS).writerow(row)
                funds_exhausted = True
                get_store().clear()
                break

            row = _case_to_row_dict(case, result, error_msg)
            with open(initial_csv, "a", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=RESULT_HEADERS).writerow(row)

            if result:
                logger.info(f"  {result.prediction.upper()} ({result.confidence})")
            elif error_msg:
                logger.error(f"  Error: {error_msg[:80]}")

            get_store().clear()

    initial_errors = _count_errors(initial_csv)
    logger.info(f"Initial run complete. Errors: {initial_errors}/{len(cases)}")

    # --- Step 2: Retry errors ---
    current_csv = initial_csv

    if initial_errors > 0 and not funds_exhausted:
        for attempt in range(1, max_retries + 1):
            logger.info("=" * 60)
            logger.info(f"STEP 2: Retry attempt {attempt}/{max_retries}")
            logger.info("=" * 60)

            errors_csv = work_dir / f"{stem}_errors_{attempt}.csv"
            retry_csv = work_dir / f"{stem}_retry_{attempt}.csv"
            merged_csv = work_dir / f"{stem}_merged_{attempt}.csv"

            error_count = extract_errors(current_csv, errors_csv)
            if error_count == 0:
                logger.info("No error rows remaining.")
                _cleanup(errors_csv)
                break

            logger.info(f"Retrying {error_count} error cases...")

            # Process error cases
            error_cases = load_cases(errors_csv, unique_districts)
            if not error_cases:
                logger.warning("Could not parse error rows as cases")
                _cleanup(errors_csv)
                break

            with open(retry_csv, "w", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=RESULT_HEADERS).writeheader()

            retry_agent = create_agent(model)
            async with retry_agent:
                for idx, case in enumerate(error_cases):
                    logger.info(f"  Retry {idx + 1}/{len(error_cases)}: {case.name}")
                    try:
                        result, error_msg = await process_case_with_retry(retry_agent, case)
                    except FundsExhausted:
                        logger.error("Funds exhausted during retry")
                        row = _case_to_row_dict(case, None, "FundsExhausted")
                        with open(retry_csv, "a", encoding="utf-8", newline="") as f:
                            csv.DictWriter(f, fieldnames=RESULT_HEADERS).writerow(row)
                        break

                    row = _case_to_row_dict(case, result, error_msg)
                    with open(retry_csv, "a", encoding="utf-8", newline="") as f:
                        csv.DictWriter(f, fieldnames=RESULT_HEADERS).writerow(row)
                    get_store().clear()

            merge_results(current_csv, retry_csv, merged_csv)
            old_csv = current_csv
            current_csv = merged_csv

            remaining = _count_errors(current_csv)
            logger.info(f"Errors remaining after retry {attempt}: {remaining}")

            # Clean up intermediate files (but not current_csv)
            _cleanup(errors_csv, retry_csv)
            if old_csv != initial_csv:
                _cleanup(old_csv)

            if remaining == 0:
                logger.info(f"All errors resolved after {attempt} retry(s).")
                break
            if remaining >= error_count:
                logger.warning("No improvement. Stopping retries.")
                break

    # --- Step 3: Run critic ---
    all_rows = _read_all_rows(current_csv)
    critic_lookup: dict[tuple, CriticResult] = {}

    try:
        if not no_critic:
            logger.info("=" * 60)
            logger.info("STEP 3: Running critic on high-confidence matches")
            logger.info("=" * 60)

            evaluated = await run_critic_on_rows(all_rows, model_id=model, matches_only=True)

            # Build lookup for merging into output
            for row, cr in evaluated:
                if cr is not None:
                    critic_lookup[row_key(row)] = cr

            # Write critic reports
            if evaluated:
                timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                report_dir = Path("quality_reports")
                report_dir.mkdir(exist_ok=True)
                report_path = report_dir / f"critic_{timestamp}.md"

                write_report(report_path, evaluated)
                write_companion_csv(report_path.with_suffix(".csv"), evaluated)
                write_passed_csv(report_path.with_name(report_path.stem + "_passed.csv"), evaluated)

                confirmed = sum(1 for _, cr in evaluated if cr and cr.grade == "CONFIRMED")
                logger.info(f"Critic report: {report_path}")
                logger.info(f"Confirmed matches: {confirmed}/{len(evaluated)}")

        # --- Step 4: Write unified output ---
        logger.info("=" * 60)
        logger.info("STEP 4: Writing unified output")
        logger.info("=" * 60)

        # Sanity check before writing — don't overwrite good data with bad
        if len(all_rows) < len(cases):
            logger.error(
                f"BUG: only {len(all_rows)} rows but expected {len(cases)}. "
                f"Keeping intermediate file at {current_csv} for recovery."
            )
            return

        _write_unified_csv(output_path, all_rows, critic_lookup)
        logger.info(f"Output: {output_path} ({len(all_rows)} rows)")

        # Cleanup intermediate files only after ALL writes succeed
        if current_csv != output_path:
            _cleanup(current_csv)
        if initial_csv != current_csv:
            _cleanup(initial_csv)

    except Exception as e:
        logger.error(f"Error in Step 3/4: {e}")
        logger.error(f"Intermediate results preserved at: {current_csv}")
        raise

    # Summary
    preds: dict[str, int] = {}
    for row in all_rows:
        p = row.get("prediction", "").strip()
        preds[p] = preds.get(p, 0) + 1

    logger.info("=" * 60)
    logger.info("SUMMARY")
    for p in ["same", "different", "uncertain", "error"]:
        if p in preds:
            logger.info(f"  {p}: {preds[p]}")
    if critic_lookup:
        logger.info(f"  critic evaluated: {len(critic_lookup)}")
        confirmed = sum(1 for cr in critic_lookup.values() if cr.grade == "CONFIRMED")
        logger.info(f"  confirmed matches: {confirmed}")
    logger.info("=" * 60)


def _cleanup(*paths: Path) -> None:
    """Remove intermediate files, ignoring missing."""
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


@click.command()
@click.argument("input_csv", type=click.Path(exists=True, path_type=Path))
@click.argument("output_csv", type=click.Path(path_type=Path))
@click.option(
    "--max-retries",
    default=10,
    show_default=True,
    type=int,
    help="Max retry rounds (stops early if no improvement)",
)
@click.option("--model", "-m", default=DEFAULT_MODEL, show_default=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.option("--unique-districts", type=int, default=None)
@click.option("--no-critic", is_flag=True, help="Skip automatic critic evaluation")
def main(
    input_csv: Path,
    output_csv: Path,
    max_retries: int,
    model: str,
    verbose: bool,
    unique_districts: Optional[int],
    no_critic: bool,
) -> None:
    """Unified pipeline: disambiguate, retry errors, run critic, output single CSV.

    \b
    Examples:
        uv run python process_with_retry.py input.csv output.csv
        uv run python process_with_retry.py input.csv output.csv --no-critic
        uv run python process_with_retry.py input.csv output.csv -m z-ai/glm-4.6
        uv run python process_with_retry.py input.csv output.csv --max-retries 3
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("UNIFIED PIPELINE")
    logger.info("=" * 60)
    logger.info(f"Input:  {input_csv}")
    logger.info(f"Output: {output_csv}")
    logger.info(f"Model:  {model}")
    logger.info(f"Retries: {max_retries}")
    logger.info(f"Critic: {'off' if no_critic else 'on'}")
    logger.info("=" * 60)

    try:
        asyncio.run(
            run_pipeline(input_csv, output_csv, model, max_retries, unique_districts, no_critic)
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    else:
        logger.info("Done.")


if __name__ == "__main__":
    main()
