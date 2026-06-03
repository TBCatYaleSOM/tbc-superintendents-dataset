"""Tests for the critic tool — models, filtering, prompt construction, report generation."""

import csv

import pytest

from critic import (
    CRITERION_NAMES,
    _is_common_name,
    build_case_prompt,
    filter_rows,
    is_suspect,
    load_results_csv,
    write_companion_csv,
    write_report,
)
from models import CriticCriterion, CriticResult

# ---------------------------------------------------------------------------
# CriticCriterion model
# ---------------------------------------------------------------------------


class TestCriticCriterion:
    def test_pass_gives_2_points(self):
        c = CriticCriterion(name="Evidence Relevance Filter", score="pass", notes="ok")
        assert c.points == 2

    def test_weak_gives_1_point(self):
        c = CriticCriterion(name="Evidence Relevance Filter", score="weak", notes="meh")
        assert c.points == 1

    def test_fail_gives_0_points(self):
        c = CriticCriterion(name="Evidence Relevance Filter", score="fail", notes="bad")
        assert c.points == 0

    def test_score_normalized_to_lowercase(self):
        c = CriticCriterion(name="test", score="PASS", notes="")
        assert c.score == "pass"
        assert c.points == 2

    def test_invalid_score_rejected(self):
        with pytest.raises(Exception):
            CriticCriterion(name="test", score="maybe", notes="")


# ---------------------------------------------------------------------------
# CriticResult model
# ---------------------------------------------------------------------------


def _make_criteria(scores: list[str]) -> list[CriticCriterion]:
    """Helper: build criteria list from a list of score strings."""
    return [
        CriticCriterion(name=CRITERION_NAMES[i], score=s, notes=f"note {i}")
        for i, s in enumerate(scores)
    ]


class TestCriticResult:
    def test_all_pass_score_20(self):
        cr = CriticResult(
            criteria=_make_criteria(["pass"] * 10),
            key_concern="none",
            would_rerun_help=False,
            rerun_reason="solid",
        )
        assert cr.total_score == 20
        assert cr.grade == "CONFIRMED"

    def test_all_fail_score_0(self):
        cr = CriticResult(
            criteria=_make_criteria(["fail"] * 10),
            key_concern="everything",
            would_rerun_help=True,
            rerun_reason="start over",
        )
        assert cr.total_score == 0
        assert cr.grade == "SUSPECT"

    def test_mixed_scores(self):
        # 5 pass (10) + 3 weak (3) + 2 fail (0) = 13
        scores = ["pass"] * 5 + ["weak"] * 3 + ["fail"] * 2
        cr = CriticResult(
            criteria=_make_criteria(scores),
            key_concern="some issues",
            would_rerun_help=False,
            rerun_reason="",
        )
        assert cr.total_score == 13
        assert cr.grade == "QUESTIONABLE"

    def test_grade_boundary_14_is_plausible(self):
        # 7 pass (14) + 3 fail (0) = 14
        scores = ["pass"] * 7 + ["fail"] * 3
        cr = CriticResult(
            criteria=_make_criteria(scores),
            key_concern="",
            would_rerun_help=False,
            rerun_reason="",
        )
        assert cr.total_score == 14
        assert cr.grade == "PLAUSIBLE"

    def test_grade_boundary_18_is_confirmed(self):
        # 9 pass (18) + 1 fail (0) = 18
        scores = ["pass"] * 9 + ["fail"] * 1
        cr = CriticResult(
            criteria=_make_criteria(scores),
            key_concern="",
            would_rerun_help=False,
            rerun_reason="",
        )
        assert cr.total_score == 18
        assert cr.grade == "CONFIRMED"

    def test_grade_boundary_9_is_suspect(self):
        # 3 pass (6) + 3 weak (3) + 4 fail (0) = 9
        scores = ["pass"] * 3 + ["weak"] * 3 + ["fail"] * 4
        cr = CriticResult(
            criteria=_make_criteria(scores),
            key_concern="",
            would_rerun_help=True,
            rerun_reason="bad",
        )
        assert cr.total_score == 9
        assert cr.grade == "SUSPECT"


# ---------------------------------------------------------------------------
# Common name detection
# ---------------------------------------------------------------------------


class TestIsCommonName:
    def test_common_name(self):
        assert _is_common_name("John Smith") is True

    def test_uncommon_name(self):
        assert _is_common_name("Emin Cavusoglu") is False

    def test_common_first_uncommon_last(self):
        assert _is_common_name("John Cavusoglu") is False

    def test_uncommon_first_common_last(self):
        assert _is_common_name("Emin Smith") is False

    def test_single_word_name(self):
        assert _is_common_name("John") is False

    def test_case_insensitive(self):
        assert _is_common_name("JOHN SMITH") is True


# ---------------------------------------------------------------------------
# Suspect heuristic
# ---------------------------------------------------------------------------


class TestIsSuspect:
    def _row(self, **overrides) -> dict:
        base = {
            "name": "Emin Cavusoglu",
            "prediction": "same",
            "confidence": "high",
            "reasoning": "x" * 300,
            "evidence_urls": "http://a.com, http://b.com, http://c.com",
        }
        base.update(overrides)
        return base

    def test_normal_case_not_suspect(self):
        assert is_suspect(self._row()) is False

    def test_common_name_same_is_suspect(self):
        assert is_suspect(self._row(name="John Smith")) is True

    def test_uncertain_is_suspect(self):
        assert is_suspect(self._row(prediction="uncertain")) is True

    def test_no_urls_is_suspect(self):
        assert is_suspect(self._row(evidence_urls="")) is True

    def test_short_reasoning_is_suspect(self):
        assert is_suspect(self._row(reasoning="too short")) is True

    def test_high_same_few_urls_is_suspect(self):
        assert is_suspect(self._row(evidence_urls="http://only.one")) is True

    def test_different_prediction_not_suspect(self):
        assert is_suspect(self._row(prediction="different")) is False


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------


class TestLoadResultsCsv:
    def test_loads_valid_rows(self, tmp_path):
        csv_path = tmp_path / "results.csv"
        csv_path.write_text(
            "name,prediction,confidence,reasoning,evidence_urls\n"
            "Alice,same,high,good reasoning here,http://a.com\n"
            "Bob,different,medium,some reasoning,http://b.com\n",
            encoding="utf-8",
        )
        rows = load_results_csv(csv_path)
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"

    def test_skips_error_rows(self, tmp_path):
        csv_path = tmp_path / "results.csv"
        csv_path.write_text(
            "name,prediction,confidence,reasoning,evidence_urls\n"
            "Alice,same,high,good,http://a.com\n"
            "Bob,error,,failed,\n"
            "Carol,different,low,ok,http://c.com\n",
            encoding="utf-8",
        )
        rows = load_results_csv(csv_path)
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"
        assert rows[1]["name"] == "Carol"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFilterRows:
    def _rows(self) -> list[dict]:
        return [
            {
                "name": "Alice Jones",
                "prediction": "same",
                "confidence": "high",
                "reasoning": "x" * 300,
                "evidence_urls": "http://a, http://b, http://c",
            },
            {
                "name": "Bob Smith",
                "prediction": "uncertain",
                "confidence": "low",
                "reasoning": "short",
                "evidence_urls": "",
            },
            {
                "name": "Carol Lee",
                "prediction": "different",
                "confidence": "high",
                "reasoning": "x" * 300,
                "evidence_urls": "http://a, http://b",
            },
        ]

    def test_case_filter(self):
        result = filter_rows(self._rows(), case_name="alice")
        assert len(result) == 1
        assert result[0]["name"] == "Alice Jones"

    def test_suspect_only(self):
        result = filter_rows(self._rows(), suspect_only=True)
        names = {r["name"] for r in result}
        assert "Bob Smith" in names  # uncertain + no urls + short reasoning

    def test_sample_limits_count(self):
        result = filter_rows(self._rows(), sample=1)
        assert len(result) == 1

    def test_sample_larger_than_rows(self):
        result = filter_rows(self._rows(), sample=100)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestBuildCasePrompt:
    def test_contains_key_fields(self):
        row = {
            "name": "John Smith",
            "leaid_name1": "Springfield SD",
            "state1": "IL",
            "year1": "2018",
            "leaid_name2": "Lincoln SD",
            "state2": "MO",
            "year2": "2021",
            "prediction": "same",
            "confidence": "high",
            "reasoning": "Found LinkedIn showing both.",
            "evidence_urls": "http://linkedin.com/in/jsmith",
        }
        prompt = build_case_prompt(row)
        assert "John Smith" in prompt
        assert "Springfield SD" in prompt
        assert "Lincoln SD" in prompt
        assert "same" in prompt
        assert "LinkedIn" in prompt

    def test_handles_missing_position3(self):
        row = {
            "name": "Test",
            "leaid_name1": "D1",
            "state1": "CA",
            "year1": "2020",
            "leaid_name2": "D2",
            "state2": "TX",
            "year2": "2022",
            "leaid_name3": "",
            "state3": "",
            "year3": "",
            "prediction": "different",
            "confidence": "medium",
            "reasoning": "reasons",
            "evidence_urls": "",
        }
        prompt = build_case_prompt(row)
        assert "Position 3" not in prompt

    def test_includes_position3_when_present(self):
        row = {
            "name": "Test",
            "leaid_name1": "D1",
            "state1": "CA",
            "year1": "2020",
            "leaid_name2": "D2",
            "state2": "TX",
            "year2": "2022",
            "leaid_name3": "D3",
            "state3": "FL",
            "year3": "2024",
            "prediction": "same",
            "confidence": "high",
            "reasoning": "reasons",
            "evidence_urls": "",
        }
        prompt = build_case_prompt(row)
        assert "Position 3: D3 (FL), 2024" in prompt


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


class TestWriteReport:
    def _make_evaluated(self) -> list[tuple[dict, CriticResult | None]]:
        row1 = {
            "name": "Alice",
            "leaid_name1": "D1",
            "leaid_name2": "D2",
            "prediction": "same",
            "confidence": "high",
        }
        cr1 = CriticResult(
            criteria=_make_criteria(["pass"] * 10),
            key_concern="none",
            would_rerun_help=False,
            rerun_reason="solid",
        )
        row2 = {
            "name": "Bob",
            "leaid_name1": "D3",
            "leaid_name2": "D4",
            "prediction": "different",
            "confidence": "low",
        }
        cr2 = CriticResult(
            criteria=_make_criteria(["fail"] * 5 + ["pass"] * 5),
            key_concern="weak evidence",
            would_rerun_help=True,
            rerun_reason="needs more sources",
        )
        return [(row1, cr1), (row2, cr2)]

    def test_report_written(self, tmp_path):
        report_path = tmp_path / "report.md"
        write_report(report_path, self._make_evaluated())
        content = report_path.read_text()
        assert "# Disambiguation Critic Report" in content
        assert "Alice" in content
        assert "Bob" in content
        assert "CONFIRMED" in content
        assert "QUESTIONABLE" in content

    def test_report_includes_rerun_recommendations(self, tmp_path):
        report_path = tmp_path / "report.md"
        write_report(report_path, self._make_evaluated())
        content = report_path.read_text()
        assert "Recommended Re-Runs" in content
        assert "needs more sources" in content


class TestWriteCompanionCsv:
    def test_csv_written(self, tmp_path):
        csv_path = tmp_path / "companion.csv"
        row = {"name": "Alice", "prediction": "same", "confidence": "high"}
        cr = CriticResult(
            criteria=_make_criteria(["pass"] * 10),
            key_concern="none",
            would_rerun_help=False,
            rerun_reason="solid",
        )
        write_companion_csv(csv_path, [(row, cr)])

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["name"] == "Alice"
        assert rows[0]["critic_score"] == "20"
        assert rows[0]["critic_grade"] == "CONFIRMED"
        assert rows[0]["c1"] == "pass"

    def test_csv_handles_error_case(self, tmp_path):
        csv_path = tmp_path / "companion.csv"
        row = {"name": "Bob", "prediction": "same", "confidence": "high"}
        write_companion_csv(csv_path, [(row, None)])

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert rows[0]["critic_score"] == "error"
        assert rows[0]["critic_grade"] == "error"
