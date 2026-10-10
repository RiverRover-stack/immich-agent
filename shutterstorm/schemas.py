"""Data contracts shared by planners, validator and runner. The source of truth for shapes.

A planner (LLM or formula) can only ever produce a `Proposal`. Nothing runs until the validator
turns it into an `ExperimentSpec`; anything it cannot make safe is rejected, never executed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Expectation = Literal["healthy", "degraded", "fail"]


class Prediction(BaseModel):
    journey: str
    expected: Expectation
    confidence: float = Field(0.5, ge=0.0, le=1.0)


class Proposal(BaseModel):
    """What a planner suggests. Untrusted until validated."""

    target: str
    fault: str
    params: dict[str, float | int | str] = Field(default_factory=dict)
    ttl_s: float = Field(30.0, gt=0)
    hypothesis: str = ""
    predictions: list[Prediction] = Field(default_factory=list)
    protected_journeys: list[str] = Field(default_factory=list)
    reason: str = ""
    planner: str = "fallback"  # fallback | llm | paste


class PlanRequest(BaseModel):
    """Everything a planner is allowed to know. Facts only, no secrets, no raw logs."""

    topology: dict
    baseline: dict | None = None
    results: list[dict] = Field(default_factory=list)
    catalog: list[dict] = Field(default_factory=list)
