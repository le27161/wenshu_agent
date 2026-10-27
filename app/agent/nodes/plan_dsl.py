"""Plan a natural-language question into the controlled business DSL."""

from langchain_core.prompts import ChatPromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.enterprise.dsl import AnalysisIntent, OrderBy, QueryDSL, TimeRange
from app.enterprise.semantic import get_catalog


PROMPT = """你是电商经营分析规划器，只能输出符合 QueryDSL schema 的 JSON。
禁止输出 SQL、表名、字段名、Join 或 Markdown。指标和维度必须从候选语义中选择。
如果问题缺少必要信息，仍选择最合理的默认意图；无法确定时抛出校验错误。

数据集: {dataset}
候选指标和维度:
{context}
用户问题: {query}
"""


async def plan_dsl(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    step = "生成DSL"
    writer({"type": "progress", "step": step, "status": "running"})
    try:
        dataset = state.get("dataset", "dw")
        catalog = get_catalog(dataset)
        candidates = {
            "metrics": [
                {"name": metric.name, "aliases": metric.aliases, "description": metric.description}
                for metric in catalog.metrics.values()
            ],
            "dimensions": [
                {"name": dim.name, "aliases": dim.aliases}
                for dim in catalog.dimensions.values()
            ],
        }
        try:
            structured = llm.with_structured_output(QueryDSL)
            result = await structured.ainvoke(
                ChatPromptTemplate.from_template(PROMPT).format_messages(
                    dataset=dataset, context=candidates, query=state["query"]
                )
            )
            if isinstance(result, QueryDSL):
                dsl = result
            else:
                dsl = QueryDSL.model_validate(result)
        except Exception as exc:
            logger.warning(f"{step} LLM failed, using deterministic fallback: {exc}")
            dsl = _fallback_dsl(dataset, state["query"])
        if dsl.dataset != dataset:
            dsl = dsl.model_copy(update={"dataset": dataset})
        logger.info(f"生成 DSL: {dsl.model_dump()}")
        writer({"type": "progress", "step": step, "status": "success"})
        writer({"type": "dsl_generated", "data": dsl.model_dump(mode="json")})
        return {"dsl": dsl.model_dump(mode="json")}
    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise


def _fallback_dsl(dataset: str, query: str) -> QueryDSL:
    text = query.lower()
    if dataset == "olist":
        metrics = ["gmv"]
        if "支付" in query or "payment" in text:
            metrics.append("payment_value")
        if "评价" in query or "review" in text or "评分" in query:
            metrics.append("review_score")
        return QueryDSL(
            dataset="olist",
            intent=AnalysisIntent.aggregate,
            metrics=metrics,
            dimensions=["product_category"],
            time_range=TimeRange(
                type="absolute", start="2017-01-01", end="2018-09-01"
            ),
            order_by=[OrderBy(field=metrics[0], direction="desc")],
            limit=20,
        )
    return QueryDSL(
        dataset="dw",
        intent=AnalysisIntent.aggregate,
        metrics=["gmv"],
        dimensions=["region"],
        time_range=TimeRange(type="absolute", start="2025-01-01", end="2025-04-01"),
        order_by=[OrderBy(field="gmv", direction="desc")],
        limit=20,
    )
