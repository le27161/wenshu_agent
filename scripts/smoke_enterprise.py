"""Enterprise smoke checks for local Docker-backed services."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
    olist_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository


async def mysql_counts() -> dict[str, Any]:
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    olist_mysql_client_manager.init()
    try:
        async with meta_mysql_client_manager.session_factory() as session:
            repo = MetaMySQLRepository(session)
            await repo.ensure_enterprise_schema()
            await session.commit()
            audit_ready = await session.execute(text("SELECT COUNT(*) FROM query_audit"))
            dataset_counts = {}
            for table in ("table_info", "column_info", "metric_info", "column_metric"):
                rows = (
                    await session.execute(
                        text(
                            f"SELECT dataset, COUNT(*) AS n FROM {table} GROUP BY dataset ORDER BY dataset"
                        )
                    )
                ).fetchall()
                dataset_counts[table] = {row[0]: int(row[1]) for row in rows}
            meta_counts = {
                "query_audit": int(audit_ready.scalar() or 0),
                "dataset_counts": dataset_counts,
            }

        async with dw_mysql_client_manager.session_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM fact_order"))
            dw_count = int(result.scalar() or 0)

        async with olist_mysql_client_manager.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                    ORDER BY table_name
                    """
                )
            )
            olist_tables = [row[0] for row in result.fetchall()]
            olist_counts = {}
            for table in olist_tables:
                count_result = await session.execute(text(f"SELECT COUNT(*) FROM `{table}`"))
                olist_counts[table] = int(count_result.scalar() or 0)
            fk_sql = {
                "orphan_items": "SELECT COUNT(*) FROM fact_order_item i LEFT JOIN fact_order o ON o.order_id=i.order_id WHERE o.order_id IS NULL",
                "orphan_payments": "SELECT COUNT(*) FROM fact_payment p LEFT JOIN fact_order o ON o.order_id=p.order_id WHERE o.order_id IS NULL",
                "orphan_reviews": "SELECT COUNT(*) FROM fact_review r LEFT JOIN fact_order o ON o.order_id=r.order_id WHERE o.order_id IS NULL",
                "orphan_products": "SELECT COUNT(*) FROM fact_order_item i LEFT JOIN dim_product p ON p.product_id=i.product_id WHERE p.product_id IS NULL",
                "orphan_sellers": "SELECT COUNT(*) FROM fact_order_item i LEFT JOIN dim_seller s ON s.seller_id=i.seller_id WHERE s.seller_id IS NULL",
            }
            olist_fk_checks = {}
            for name, sql in fk_sql.items():
                result = await session.execute(text(sql))
                olist_fk_checks[name] = int(result.scalar() or 0)

        return {
            "meta": meta_counts,
            "dw": {"fact_order": dw_count},
            "olist": {
                "row_counts": olist_counts,
                "foreign_key_checks": olist_fk_checks,
            },
        }
    finally:
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
        await olist_mysql_client_manager.close()


async def vector_services() -> dict[str, Any]:
    qdrant_client_manager.init()
    es_client_manager.init()
    embedding_client_manager.init()
    try:
        collections = await qdrant_client_manager.client.get_collections()
        es_health = await es_client_manager.client.cluster.health()
        embedding = await embedding_client_manager.client.aembed_query("smoke")
        return {
            "qdrant_collections": [c.name for c in collections.collections],
            "es_status": es_health.get("status"),
            "embedding_dim": len(embedding),
        }
    finally:
        await qdrant_client_manager.close()
        await es_client_manager.close()


async def api_sse(api_url: str, dataset: str) -> dict[str, Any]:
    payload = {
        "query": "统计 2025 年第一季度各大区 GMV",
        "dataset": dataset,
        "principal": {"roles": ["admin"], "allowed_datasets": ["dw", "olist"]},
    }
    events = []
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", api_url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if raw:
                    events.append(json.loads(raw))
                if events and events[-1].get("type") in {"result", "error"}:
                    break
    return {
        "event_count": len(events),
        "event_types": [event.get("type") for event in events],
        "last_event": events[-1] if events else None,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/query")
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--dataset", default="dw", choices=["dw", "olist"])
    args = parser.parse_args()

    report = {
        "mysql": await mysql_counts(),
        "vector_services": await vector_services(),
    }
    if not args.skip_api:
        report["api_sse"] = await api_sse(args.api_url, args.dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
