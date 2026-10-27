"""Final SQL AST security check after policy rewriting."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.enterprise.ast_gateway import ASTPolicyGateway
from app.enterprise.policy import Principal


async def validate_ast(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    step = "AST安全校验"
    writer({"type": "progress", "step": step, "status": "running"})
    raw = state.get("principal") or {"roles": ["admin"]}
    principal = Principal(roles=tuple(raw.get("roles", ["admin"])), region_ids=tuple(raw.get("region_ids", [])), allowed_datasets=tuple(raw.get("allowed_datasets", ["dw", "olist"])))
    ASTPolicyGateway().validate(state["sql"], principal, state.get("dataset", "dw"))
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "ast_validated", "data": {"read_only": True}})
    return {"error": None, "ast_validation": {"read_only": True, "row_scope_present": True}}
