"""Validate DSL against the selected dataset semantic catalog."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.enterprise.dsl import QueryDSL
from app.enterprise.semantic import get_catalog


async def validate_dsl(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    step = "校验DSL"
    writer({"type": "progress", "step": step, "status": "running"})
    dsl = QueryDSL.model_validate(state["dsl"])
    catalog = get_catalog(dsl.dataset)
    errors = catalog.validate(dsl)
    if errors:
        writer({"type": "progress", "step": step, "status": "error"})
        writer({"type": "query_rejected", "reason": "DSL_VALIDATION", "details": errors})
        raise ValueError("; ".join(errors))
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "dsl_validated", "data": dsl.model_dump(mode="json")})
    return {"dsl": dsl.model_dump(mode="json")}

