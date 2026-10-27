"""Compile validated DSL into deterministic SQL."""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.enterprise.compiler import DSLCompiler
from app.enterprise.dsl import QueryDSL
from app.enterprise.semantic import get_catalog


async def compile_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    step = "编译SQL"
    writer({"type": "progress", "step": step, "status": "running"})
    dsl = QueryDSL.model_validate(state["dsl"])
    compiled = DSLCompiler().compile(dsl, get_catalog(dsl.dataset))
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "sql_compiled", "sql": compiled.sql, "explanation": compiled.explanation})
    return {"sql": compiled.sql, "compiled_sql": compiled.sql, "compile_explanation": compiled.explanation}

