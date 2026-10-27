"""Apply row/column policies and validate the resulting SQL AST."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.enterprise.ast_gateway import ASTPolicyGateway
from app.enterprise.policy import Principal


async def apply_policy(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    step = "注入行列权限"
    writer({"type": "progress", "step": step, "status": "running"})
    raw = state.get("principal") or {"roles": ["admin"]}
    principal = Principal(
        user_id=raw.get("user_id", "demo-user"), tenant_id=raw.get("tenant_id", "demo-tenant"),
        roles=tuple(raw.get("roles", ["admin"])), store_ids=tuple(raw.get("store_ids", [])),
        region_ids=tuple(raw.get("region_ids", [])), allowed_datasets=tuple(raw.get("allowed_datasets", ["dw", "olist"])),
    )
    result = ASTPolicyGateway().protect(state["sql"], principal, state.get("dataset", "dw"))
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "permission_applied", "data": {"row_predicates": result.policy.row_predicates, "masked_columns": result.policy.masked_columns}})
    return {"sql": result.sql, "final_sql": result.sql, "policy": {"row_predicates": result.policy.row_predicates, "masked_columns": result.policy.masked_columns}}

