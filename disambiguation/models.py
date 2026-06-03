"""Data models for superintendent disambiguation tool."""

from typing import Literal

from pydantic import BaseModel, computed_field, field_validator


class SuperintendentPosition(BaseModel):
    """A single position record for a superintendent."""

    year: int | str  # Can be a single year (int) or year range (str like "2007-2014")
    state: str
    leaid: int
    district_name: str
    name_raw: str | None = None


class SuperintendentCase(BaseModel):
    """Input model for a disambiguation case."""

    name: str
    position1: SuperintendentPosition
    position2: SuperintendentPosition
    position3: SuperintendentPosition | None = None


class DisambiguationResult(BaseModel):
    """Output model for agent's disambiguation determination."""

    prediction: Literal["same", "different", "uncertain"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    evidence_urls: list[str]

    @field_validator("prediction", "confidence", mode="before")
    @classmethod
    def normalize_lowercase(cls, v: str) -> str:
        """Normalize prediction and confidence values to lowercase."""
        if isinstance(v, str):
            return v.lower()
        return v


# --- Critic models ---

SCORE_POINTS = {"pass": 2, "weak": 1, "fail": 0}


class CriticCriterion(BaseModel):
    """A single criterion evaluation from the critic."""

    name: str
    score: Literal["pass", "weak", "fail"]
    notes: str

    @field_validator("score", mode="before")
    @classmethod
    def normalize_score(cls, v: str) -> str:
        if isinstance(v, str):
            return v.lower()
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def points(self) -> int:
        return SCORE_POINTS[self.score]


class CriticResult(BaseModel):
    """Output model for the critic agent's evaluation of a single case."""

    criteria: list[CriticCriterion]
    key_concern: str
    would_rerun_help: bool
    rerun_reason: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_score(self) -> int:
        return sum(c.points for c in self.criteria)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        s = self.total_score
        if s >= 18:
            return "CONFIRMED"
        if s >= 14:
            return "PLAUSIBLE"
        if s >= 10:
            return "QUESTIONABLE"
        return "SUSPECT"
