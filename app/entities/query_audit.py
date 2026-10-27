"""Structured audit record for governed analytical queries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class QueryAudit:
    """系统内部统一使用的查询审计表达。"""

    request_id: str
    user_id: str
    tenant_id: str
    query: str | None
    dataset: str
    dsl: dict[str, Any] | None
    compiled_sql: str | None
    final_sql: str | None
    policy: dict[str, Any] | None
    ast_validation: dict[str, Any] | None
    row_count: int
    duration_ms: float | None
    created_at: datetime
