"""Automated retry script for error cases."""
import csv
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def normalize_id(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        if "." in text:
            as_float = float(text)
            if as_float.is_integer():
                return str(int(as_float))
        return str(int(text))
    except ValueError:
        return text


def row_key(row: dict) -> tuple[str, str, str]:
    name = str(row.get("name", "")).strip().lower()
    leaid1 = normalize_id(row.get("leaid1"))
    leaid2 = normalize_id(row.get("leaid2"))
    return name, leaid1, leaid2


def extract_errors(input_csv: Path, error_csv: Path) -> int:
    error_count = 0
    with open(input_csv, "r", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        fieldnames = reader.fieldnames
        if not fieldnames:
            return 0
        with open(error_csv, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                if row.get("prediction", "").strip().lower() == "error":
                    writer.writerow(row)
                    error_count += 1
    return error_count


def merge_results(original_csv: Path, retry_csv: Path, merged_csv: Path) -> None:
    retry_results: dict[tuple[str, str, str], dict] = {}
    with open(retry_csv, "r", encoding="utf-8") as retry_file:
        retry_reader = csv.DictReader(retry_file)
        for row in retry_reader:
            if row.get("prediction", "").strip().lower() == "error":
                continue
            retry_results[row_key(row)] = row

    with open(original_csv, "r", encoding="utf-8") as original_file:
        original_reader = csv.DictReader(original_file)
        fieldnames = original_reader.fieldnames
        if not fieldnames:
            return
        with open(merged_csv, "w", encoding="utf-8", newline="") as merged_file:
            writer = csv.DictWriter(merged_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in original_reader:
                key = row_key(row)
                if key in retry_results:
                    writer.writerow(retry_results[key])
                else:
                    writer.writerow(row)


def run_command(cmd: list[str], description: str) -> bool:
    logger.info("%s: %s", description, " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
        return True
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        return False


@click.command()
@click.argument("input_csv", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", "output_csv", type=click.Path(path_type=Path))
@click.option("--merge", is_flag=True, help="Merge successful retries back in")
@click.option("--merge-output", type=click.Path(path_type=Path))
@click.option("--model", "-m", default="z-ai/glm-4.6", show_default=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.option("--unique-districts", type=int, default=None)
def main(
    input_csv: Path,
    output_csv: Optional[Path],
    merge: bool,
    merge_output: Optional[Path],
    model: str,
    verbose: bool,
    unique_districts: Optional[int],
) -> None:
    if output_csv is None:
        output_csv = input_csv.parent / f"{input_csv.stem}_retry.csv"
    if merge_output is None:
        merge_output = input_csv.parent / f"{input_csv.stem}_merged.csv"

    errors_input = input_csv.parent / f"{input_csv.stem}_errors.csv"
    error_count = extract_errors(input_csv, errors_input)

    if error_count == 0:
        logger.info("No error rows found. Nothing to retry.")
        try:
            errors_input.unlink()
        except FileNotFoundError:
            pass
        return

    logger.info("Found %s error rows. Running retry...", error_count)
    cmd = [
        "uv",
        "run",
        "python",
        "main.py",
        "process",
        str(errors_input),
        str(output_csv),
        "--model",
        model,
    ]
    if unique_districts is not None:
        cmd.extend(["--unique-districts", str(unique_districts)])
    if verbose:
        cmd.append("-v")

    if not run_command(cmd, "Retry run"):
        sys.exit(1)

    if merge:
        logger.info("Merging results to %s", merge_output)
        merge_results(input_csv, output_csv, merge_output)

    try:
        errors_input.unlink()
    except FileNotFoundError:
        pass

    logger.info("Done.")


if __name__ == "__main__":
    main()
