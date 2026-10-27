"""受控的电商分析 DSL。

LLM 只负责生成本模块定义的业务意图，后续 SQL 由确定性编译器生成。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalysisIntent(StrEnum):
    aggregate = "aggregate"
    trend = "trend"
    ranking = "ranking"
    comparison = "comparison"
    detail = "detail"


class FilterOperator(StrEnum):
    eq = "eq"
    neq = "neq"
    contains = "contains"
    in_ = "in"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    between = "between"


class DSLFilter(BaseModel):
    field: str = Field(min_length=1, max_length=128)
    operator: FilterOperator
    value: Any


class TimeRange(BaseModel):
    type: str = Field(pattern="^(absolute|relative)$")
    start: str | None = None
    end: str | None = None
    value: int | None = Field(default=None, ge=1, le=3660)
    unit: str | None = Field(default=None, pattern="^(day|week|month|quarter|year)$")
    time_field: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "TimeRange":
        if self.type == "absolute" and (not self.start or not self.end):
            raise ValueError("absolute time range requires start and end")
        if self.type == "relative" and (self.value is None or not self.unit):
            raise ValueError("relative time range requires value and unit")
        return self


class Comparison(BaseModel):
    type: str = Field(pattern="^(yoy|mom|wow|previous_period)$")


class OrderBy(BaseModel):
    field: str = Field(min_length=1, max_length=128)
    direction: str = Field(default="desc", pattern="^(asc|desc)$")


class OutputSpec(BaseModel):
    format: str = Field(default="table", pattern="^(table|chart|summary)$")


class QueryDSL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="1.0", pattern=r"^1\.0$")
    dataset: str = Field(default="dw", pattern="^[a-z][a-z0-9_]{0,31}$")
    intent: AnalysisIntent
    metrics: list[str] = Field(min_length=1, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    filters: list[DSLFilter] = Field(default_factory=list, max_length=30)
    time_range: TimeRange | None = None
    comparison: Comparison | None = None
    order_by: list[OrderBy] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=100, ge=1, le=1000)
    output: OutputSpec = Field(default_factory=OutputSpec)

    @model_validator(mode="after")
    def validate_intent(self) -> "QueryDSL":
        if self.intent in {AnalysisIntent.trend, AnalysisIntent.comparison} and not self.time_range:
            raise ValueError("trend/comparison queries require time_range")
        if self.intent == AnalysisIntent.ranking and not self.order_by:
            raise ValueError("ranking queries require order_by")
        if self.comparison and self.intent not in {AnalysisIntent.comparison, AnalysisIntent.trend}:
            raise ValueError("comparison is only valid for trend/comparison intent")
        return self

