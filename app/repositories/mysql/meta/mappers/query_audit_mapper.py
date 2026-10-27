"""QueryAudit 映射器。"""

from dataclasses import asdict

from app.entities.query_audit import QueryAudit
from app.models.query_audit import QueryAuditMySQL


class QueryAuditMapper:
    """负责 `QueryAudit` 与 `QueryAuditMySQL` 之间的转换。"""

    @staticmethod
    def to_model(entity: QueryAudit) -> QueryAuditMySQL:
        return QueryAuditMySQL(**asdict(entity))

    @staticmethod
    def to_entity(model: QueryAuditMySQL) -> QueryAudit:
        return QueryAudit(
            request_id=model.request_id,
            user_id=model.user_id or "",
            tenant_id=model.tenant_id or "",
            query=model.query,
            dataset=model.dataset,
            dsl=model.dsl,
            compiled_sql=model.compiled_sql,
            final_sql=model.final_sql,
            policy=model.policy,
            ast_validation=model.ast_validation,
            row_count=model.row_count,
            duration_ms=model.duration_ms,
            created_at=model.created_at,
        )
