"""Persist a structured audit envelope for every governed query."""

from datetime import datetime, timezone
from uuid import uuid4

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from app.entities.query_audit import QueryAudit


async def audit_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    started = state.get("started_at")
    audit = {
        "request_id": state.get("request_id", str(uuid4())),
        "user_id": (state.get("principal") or {}).get("user_id", "demo-user"),
        "tenant_id": (state.get("principal") or {}).get("tenant_id", "demo-tenant"),
        "query": state.get("query"),
        "dataset": state.get("dataset", "dw"),
        "dsl": state.get("dsl"),
        "compiled_sql": state.get("compiled_sql"),
        "final_sql": state.get("final_sql") or state.get("sql"),
        "policy": state.get("policy"),
        "ast_validation": state.get("ast_validation"),
        "row_count": state.get("row_count", 0),
        "created_at": datetime.now(timezone.utc),
    }
    if started:
        audit["duration_ms"] = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
    logger.info("query_audit=%s", audit)
    await runtime.context["meta_mysql_repository"].save_query_audit(
        QueryAudit(**audit)
    )
    await runtime.context["meta_mysql_repository"].session.commit()
    event_data = {**audit, "created_at": audit["created_at"].isoformat()}
    writer({"type": "audit_recorded", "data": event_data})
    writer({"type": "result", "data": state.get("result", [])})
    return {"audit": event_data}
