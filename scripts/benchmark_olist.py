"""Build and run a deterministic 100-case Olist benchmark.

This benchmark validates the controlled path:

natural-language question -> curated DSL -> deterministic SQL compiler -> MySQL.

It intentionally avoids invoking the LLM by default so that the full 100-case
suite is cheap, repeatable, and suitable for CI-style regression checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.mysql_client_manager import olist_mysql_client_manager
from app.enterprise.compiler import DSLCompiler
from app.enterprise.dsl import QueryDSL
from app.enterprise.semantic import get_catalog


METRIC_LABELS = {
    "gmv": "销售额",
    "order_count": "订单数",
    "item_count": "商品件数",
    "aov": "客单价",
    "payment_value": "支付金额",
    "delivery_days": "平均配送天数",
    "review_score": "平均评价分",
    "low_review_rate": "低评分率",
    "cancel_rate": "取消率",
    "seller_order_count": "商家订单量",
}

DIMENSION_LABELS = {
    "product_category": "商品品类",
    "seller": "卖家",
    "seller_state": "卖家州",
    "customer_state": "客户州",
    "payment_type": "支付方式",
    "order_status": "订单状态",
    "order_date": "下单日期",
    "product": "商品",
}

TIME_RANGES = [
    ("2017 全年", "2017-01-01", "2018-01-01"),
    ("2018 年 1-8 月", "2018-01-01", "2018-09-01"),
    ("2017 年上半年", "2017-01-01", "2017-07-01"),
    ("2017 年下半年", "2017-07-01", "2018-01-01"),
    ("2018 年上半年", "2018-01-01", "2018-07-01"),
    ("2017 年二季度", "2017-04-01", "2017-07-01"),
    ("2017 年四季度", "2017-10-01", "2018-01-01"),
    ("2018 年一季度", "2018-01-01", "2018-04-01"),
    ("2018 年二季度", "2018-04-01", "2018-07-01"),
    ("Olist 全周期", "2017-01-01", "2018-09-01"),
]


def time_range(index: int) -> tuple[str, dict[str, str]]:
    label, start, end = TIME_RANGES[index % len(TIME_RANGES)]
    return label, {"type": "absolute", "start": start, "end": end}


def metric_phrase(metrics: list[str]) -> str:
    return "、".join(METRIC_LABELS[item] for item in metrics)


def dimension_phrase(dimensions: list[str]) -> str:
    if not dimensions:
        return "整体"
    return "、".join(DIMENSION_LABELS[item] for item in dimensions)


def filter_phrase(filters: list[dict[str, Any]]) -> str:
    if not filters:
        return ""
    pieces = []
    for item in filters:
        field = DIMENSION_LABELS.get(item["field"], item["field"])
        value = item["value"]
        operator = item["operator"]
        if operator == "in":
            pieces.append(f"{field}属于{','.join(str(v) for v in value)}")
        elif operator == "contains":
            pieces.append(f"{field}包含{value}")
        else:
            pieces.append(f"{field}为{value}")
    return "，条件：" + "；".join(pieces)


def make_case(
    case_id: int,
    metrics: list[str],
    dimensions: list[str],
    filters: list[dict[str, Any]] | None = None,
    time_index: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    filters = filters or []
    label, period = time_range(time_index)
    first_order_field = dimensions[0] if dimensions and dimensions[0] == "order_date" else metrics[0]
    order_direction = "asc" if first_order_field == "order_date" else "desc"
    dsl = {
        "version": "1.0",
        "dataset": "olist",
        "intent": "ranking" if dimensions else "aggregate",
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": filters,
        "time_range": period,
        "order_by": [{"field": first_order_field, "direction": order_direction}],
        "limit": limit,
        "output": {"format": "table"},
    }
    target = f"按{dimension_phrase(dimensions)}统计" if dimensions else "统计整体"
    question = (
        f"{target}{label}的{metric_phrase(metrics)}"
        f"{filter_phrase(filters)}，按{METRIC_LABELS.get(first_order_field, DIMENSION_LABELS.get(first_order_field, first_order_field))}"
        f"{'升序' if order_direction == 'asc' else '降序'}返回前 {limit} 条。"
    )
    return {
        "id": f"olist-{case_id:03d}",
        "dataset": "olist",
        "question": question,
        "dsl": dsl,
        "assertions": {
            "expected_columns": dimensions + metrics,
            "min_rows": 1,
        },
    }


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(
        metrics: list[str],
        dimensions: list[str],
        filters: list[dict[str, Any]] | None = None,
        limit: int = 20,
    ) -> None:
        if len(cases) >= 100:
            return
        cases.append(
            make_case(
                len(cases) + 1,
                metrics=metrics,
                dimensions=dimensions,
                filters=filters,
                time_index=len(cases),
                limit=limit,
            )
        )

    default_dimensions = [
        "product_category",
        "customer_state",
        "seller_state",
        "payment_type",
        "order_status",
        "order_date",
    ]
    for metric in [
        "gmv",
        "order_count",
        "item_count",
        "aov",
        "payment_value",
        "delivery_days",
        "review_score",
        "low_review_rate",
        "cancel_rate",
    ]:
        for dimension in default_dimensions:
            add([metric], [dimension])

    for dimension in ["seller_state", "product_category", "seller"]:
        add(["seller_order_count"], [dimension])

    multi_metric_groups = [
        (["gmv", "item_count"], ["product_category"]),
        (["gmv", "item_count"], ["customer_state"]),
        (["gmv", "item_count"], ["seller_state"]),
        (["gmv", "item_count"], ["order_status"]),
        (["gmv", "order_count", "aov"], ["product_category"]),
        (["gmv", "order_count", "aov"], ["customer_state"]),
        (["gmv", "order_count", "aov"], ["order_date"]),
        (["payment_value", "gmv"], ["payment_type"]),
        (["payment_value", "order_count"], ["payment_type"]),
        (["payment_value", "gmv"], ["customer_state"]),
        (["review_score", "low_review_rate"], ["product_category"]),
        (["review_score", "low_review_rate"], ["customer_state"]),
        (["review_score", "low_review_rate"], ["seller_state"]),
        (["delivery_days", "review_score"], ["customer_state"]),
        (["delivery_days", "review_score"], ["order_status"]),
        (["cancel_rate", "order_count"], ["order_status"]),
        (["seller_order_count", "gmv"], ["seller_state"]),
        (["seller_order_count", "gmv"], ["product_category"]),
    ]
    for metrics, dimensions in multi_metric_groups:
        add(metrics, dimensions)

    filter_cases = [
        (["gmv"], ["product_category"], [{"field": "order_status", "operator": "eq", "value": "delivered"}]),
        (["order_count"], ["customer_state"], [{"field": "order_status", "operator": "eq", "value": "delivered"}]),
        (["item_count"], ["seller_state"], [{"field": "order_status", "operator": "eq", "value": "delivered"}]),
        (["aov"], ["payment_type"], [{"field": "order_status", "operator": "eq", "value": "delivered"}]),
        (["payment_value"], ["payment_type"], [{"field": "payment_type", "operator": "eq", "value": "credit_card"}]),
        (["payment_value"], ["customer_state"], [{"field": "payment_type", "operator": "eq", "value": "boleto"}]),
        (["review_score"], ["product_category"], [{"field": "customer_state", "operator": "eq", "value": "SP"}]),
        (["low_review_rate"], ["customer_state"], [{"field": "seller_state", "operator": "eq", "value": "SP"}]),
        (["delivery_days"], ["customer_state"], [{"field": "customer_state", "operator": "eq", "value": "RJ"}]),
        (["cancel_rate"], ["customer_state"], [{"field": "order_status", "operator": "in", "value": ["delivered", "canceled"]}]),
        (["gmv", "order_count"], ["product_category"], [{"field": "product_category", "operator": "eq", "value": "beleza_saude"}]),
        (["gmv", "item_count"], ["product_category"], [{"field": "product_category", "operator": "eq", "value": "cama_mesa_banho"}]),
        (["gmv", "item_count"], ["seller_state"], [{"field": "seller_state", "operator": "in", "value": ["SP", "MG", "PR"]}]),
        (["payment_value", "gmv"], ["payment_type"], [{"field": "payment_type", "operator": "in", "value": ["credit_card", "boleto"]}]),
        (["review_score", "low_review_rate"], ["customer_state"], [{"field": "customer_state", "operator": "in", "value": ["SP", "RJ", "MG"]}]),
        (["seller_order_count"], ["seller_state"], [{"field": "seller_state", "operator": "eq", "value": "SP"}]),
        (["seller_order_count", "gmv"], ["product_category"], [{"field": "seller_state", "operator": "eq", "value": "SP"}]),
        (["gmv"], [], [{"field": "customer_state", "operator": "eq", "value": "SP"}]),
        (["order_count"], [], [{"field": "payment_type", "operator": "eq", "value": "credit_card"}]),
        (["review_score"], [], [{"field": "order_status", "operator": "eq", "value": "delivered"}]),
        (["gmv", "payment_value", "review_score"], ["customer_state"], [{"field": "customer_state", "operator": "in", "value": ["SP", "RJ", "MG"]}]),
        (["gmv", "payment_value", "review_score"], ["product_category"], [{"field": "order_status", "operator": "eq", "value": "delivered"}]),
        (["gmv", "payment_value", "low_review_rate"], ["payment_type"], [{"field": "payment_type", "operator": "eq", "value": "credit_card"}]),
        (["delivery_days", "cancel_rate"], ["order_status"], [{"field": "order_status", "operator": "in", "value": ["delivered", "canceled"]}]),
        (["gmv", "order_count", "review_score"], ["order_date"], [{"field": "order_status", "operator": "eq", "value": "delivered"}], 30),
    ]
    for item in filter_cases:
        metrics, dimensions, filters, *rest = item
        add(metrics, dimensions, filters, rest[0] if rest else 20)

    if len(cases) != 100:
        raise RuntimeError(f"expected 100 benchmark cases, got {len(cases)}")
    return cases


async def run_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    catalog = get_catalog("olist")
    compiler = DSLCompiler()
    results: list[dict[str, Any]] = []

    olist_mysql_client_manager.init()
    started_at = time.perf_counter()
    try:
        async with olist_mysql_client_manager.session_factory() as session:
            for case in cases:
                case_started_at = time.perf_counter()
                result: dict[str, Any] = {
                    "id": case["id"],
                    "question": case["question"],
                    "status": "pass",
                    "compile_ms": None,
                    "execute_ms": None,
                    "row_count": 0,
                    "columns": [],
                    "sample": [],
                    "error": None,
                }
                try:
                    compile_started_at = time.perf_counter()
                    dsl = QueryDSL.model_validate(case["dsl"])
                    compiled = compiler.compile(dsl, catalog)
                    result["compile_ms"] = round(
                        (time.perf_counter() - compile_started_at) * 1000, 2
                    )

                    execute_started_at = time.perf_counter()
                    cursor = await session.execute(text(compiled.sql))
                    rows = cursor.mappings().all()
                    result["execute_ms"] = round(
                        (time.perf_counter() - execute_started_at) * 1000, 2
                    )
                    result["row_count"] = len(rows)
                    result["columns"] = list(rows[0].keys()) if rows else []
                    result["sample"] = [dict(row) for row in rows[:3]]
                    result["sql"] = compiled.sql

                    expected_columns = case["assertions"]["expected_columns"]
                    min_rows = case["assertions"].get("min_rows", 1)
                    if result["columns"] != expected_columns:
                        result["status"] = "fail"
                        result["error"] = (
                            f"columns mismatch: expected {expected_columns}, "
                            f"got {result['columns']}"
                        )
                    elif result["row_count"] < min_rows:
                        result["status"] = "fail"
                        result["error"] = (
                            f"row count {result['row_count']} below minimum {min_rows}"
                        )
                except Exception as exc:  # noqa: BLE001 - benchmark records all failures.
                    result["status"] = "fail"
                    result["error"] = repr(exc)
                    if result["compile_ms"] is None:
                        result["compile_ms"] = round(
                            (time.perf_counter() - case_started_at) * 1000, 2
                        )
                results.append(result)
    finally:
        await olist_mysql_client_manager.close()

    total_ms = round((time.perf_counter() - started_at) * 1000, 2)
    failed = [item for item in results if item["status"] != "pass"]
    return {
        "dataset": "olist",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "pass_rate": round((len(cases) - len(failed)) / len(cases), 4) if cases else 0,
        "total_ms": total_ms,
        "avg_execute_ms": round(
            sum((item["execute_ms"] or 0) for item in results) / len(results), 2
        )
        if results
        else 0,
        "failed_cases": [
            {"id": item["id"], "question": item["question"], "error": item["error"]}
            for item in failed
        ],
        "results": results,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-out", default="benchmarks/olist_100_cases.json")
    parser.add_argument("--report-out", default="benchmarks/reports/olist_100_report.json")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    cases = build_cases()[: args.limit]
    report = await run_cases(cases)
    cases_path = PROJECT_ROOT / args.cases_out
    report_path = PROJECT_ROOT / args.report_out
    write_json(cases_path, cases)
    write_json(report_path, report)
    print(
        json.dumps(
            {
                "dataset": report["dataset"],
                "case_count": report["case_count"],
                "passed": report["passed"],
                "failed": report["failed"],
                "pass_rate": report["pass_rate"],
                "total_ms": report["total_ms"],
                "avg_execute_ms": report["avg_execute_ms"],
                "cases_out": str(cases_path),
                "report_out": str(report_path),
                "failed_cases": report["failed_cases"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
