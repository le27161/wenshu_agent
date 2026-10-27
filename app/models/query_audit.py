"""`query_audit` ORM model for enterprise query audit persistence."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class QueryAuditMySQL(Base):
    """查询审计表对应的 ORM 模型。"""

    __tablename__ = "query_audit"

    request_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="请求编号"
    )
    user_id: Mapped[str | None] = mapped_column(String(128), comment="用户编号")
    tenant_id: Mapped[str | None] = mapped_column(String(128), comment="租户编号")
    query: Mapped[str | None] = mapped_column(Text, comment="自然语言问题")
    dataset: Mapped[str] = mapped_column(String(32), comment="数据集")
    dsl: Mapped[dict[str, Any] | None] = mapped_column(JSON, comment="业务 DSL")
    compiled_sql: Mapped[str | None] = mapped_column(Text, comment="编译 SQL")
    final_sql: Mapped[str | None] = mapped_column(Text, comment="最终执行 SQL")
    policy: Mapped[dict[str, Any] | None] = mapped_column(JSON, comment="权限策略")
    ast_validation: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, comment="AST 校验结果"
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0, comment="返回行数")
    duration_ms: Mapped[float | None] = mapped_column(Float, comment="耗时毫秒")
    created_at: Mapped[datetime] = mapped_column(DateTime, comment="创建时间")
