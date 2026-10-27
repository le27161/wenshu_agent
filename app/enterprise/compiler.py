"""Deterministic compilation of the business DSL into MySQL SQL."""

from __future__ import annotations

from dataclasses import dataclass

from app.enterprise.dsl import AnalysisIntent, FilterOperator, QueryDSL
from app.enterprise.semantic import DimensionDefinition, MetricDefinition, SemanticCatalog


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    dataset: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...]
    tables: tuple[str, ...]
    joins: tuple[str, ...]
    explanation: dict


class DSLCompiler:
    """Compile only validated DSL values; no LLM output is accepted here."""

    def compile(self, dsl: QueryDSL, catalog: SemanticCatalog) -> CompiledQuery:
        errors = catalog.validate(dsl)
        if errors:
            raise ValueError("DSL validation failed: " + "; ".join(errors))

        metric_defs = [catalog.metric(name) for name in dsl.metrics]
        dim_defs = [catalog.dimension(name) for name in dsl.dimensions]
        tables = {m.source_table for m in metric_defs} | {d.source_table for d in dim_defs}
        if not dsl.time_range:
            raise ValueError("time_range is required before SQL compilation")
        if catalog.dataset == "olist":
            sql = self._compile_olist(dsl, metric_defs, dim_defs, catalog)
        else:
            sql = self._compile_dw(dsl, metric_defs, dim_defs)

        return CompiledQuery(
            sql=sql,
            dataset=catalog.dataset,
            metrics=tuple(m.name for m in metric_defs),
            dimensions=tuple(d.name for d in dim_defs),
            tables=tuple(sorted(tables)),
            joins=catalog.joins,
            explanation={
                "metrics": [m.name for m in metric_defs],
                "dimensions": [d.name for d in dim_defs],
                "time_field": metric_defs[0].time_field,
                "default_filters": [m.default_filter for m in metric_defs if m.default_filter],
                "joins": list(catalog.joins),
                "entities": {
                    name: {
                        "table": entity.get("table"),
                        "alias": entity.get("alias"),
                        "role": entity.get("role"),
                    }
                    for name, entity in catalog.entities.items()
                },
                "relationships": list(catalog.relationships),
                "metric_definitions": [
                    {
                        "name": metric.name,
                        "source_entity": metric.source_entity,
                        "source_table": metric.source_table,
                        "family": metric.family,
                        "expression": metric.expression,
                    }
                    for metric in metric_defs
                ],
            },
        )

    def _compile_olist(
        self,
        dsl: QueryDSL,
        metrics: list[MetricDefinition],
        dims: list[DimensionDefinition],
        catalog: SemanticCatalog,
    ) -> str:
        families: dict[str, list[MetricDefinition]] = {}
        for metric in metrics:
            families.setdefault(self._olist_metric_family(metric), []).append(metric)

        ctes = []
        for index, (_, family_metrics) in enumerate(families.items()):
            cte_name = f"metric_{index}"
            required_entities = {
                metric.source_entity
                for metric in family_metrics
                if metric.source_entity
            }
            required_entities.update(
                dim.source_entity for dim in dims if dim.source_entity
            )
            required_entities.update(self._olist_filter_sources(dsl, catalog))
            tables = self._olist_from_clause(required_entities, catalog)

            select_parts = [f"{dim.expression} AS {dim.name}" for dim in dims]
            select_parts.extend(
                f"{metric.expression} AS {metric.name}" for metric in family_metrics
            )
            where = self._olist_where(dsl, family_metrics, catalog)
            group = f" GROUP BY {', '.join(dim.name for dim in dims)}" if dims else ""
            ctes.append(
                f"{cte_name} AS (SELECT {', '.join(select_parts)} FROM {tables} WHERE {' AND '.join(where)}{group})"
            )

        if not ctes:
            raise ValueError("at least one metric is required")

        metric_locations = {
            metric.name: f"metric_{index}"
            for index, (_, family_metrics) in enumerate(families.items())
            for metric in family_metrics
        }
        metric_defs_by_name = {metric.name: metric for metric in metrics}

        first = "metric_0"
        select_parts = [f"{first}.{dim.name} AS {dim.name}" for dim in dims]
        for metric_name in dsl.metrics:
            metric = metric_defs_by_name[metric_name.lower()]
            select_parts.append(f"{metric_locations[metric.name]}.{metric.name} AS {metric.name}")

        from_clause = first
        for index in range(1, len(ctes)):
            cte_name = f"metric_{index}"
            if dims:
                predicates = [
                    f"{first}.{dim.name} = {cte_name}.{dim.name}" for dim in dims
                ]
                from_clause += f" LEFT JOIN {cte_name} ON {' AND '.join(predicates)}"
            else:
                from_clause += f" CROSS JOIN {cte_name}"

        order = self._order_by(dsl, metrics, dims)
        return f"WITH {', '.join(ctes)} SELECT {', '.join(select_parts)} FROM {from_clause}{order} LIMIT {dsl.limit}"

    @staticmethod
    def _olist_metric_family(metric: MetricDefinition) -> str:
        if metric.family:
            return metric.family
        if metric.source_table == "fact_payment":
            return "payment"
        if metric.source_table == "fact_review":
            return "review"
        if metric.source_table == "fact_order_item":
            return "item"
        return "order"

    def _olist_where(
        self,
        dsl: QueryDSL,
        metrics: list[MetricDefinition],
        catalog: SemanticCatalog,
    ) -> list[str]:
        where = ["1=1"]
        if dsl.time_range:
            time_field = dsl.time_range.time_field or metrics[0].time_field
            if dsl.time_range.type == "absolute":
                where.append(
                    f"{time_field} >= '{dsl.time_range.start}' AND {time_field} < '{dsl.time_range.end}'"
                )
            else:
                unit = dsl.time_range.unit
                where.append(
                    f"{time_field} >= DATE_SUB(CURDATE(), INTERVAL {dsl.time_range.value} {unit.upper()})"
                )
        for metric in metrics:
            if metric.default_filter:
                where.append(metric.default_filter)
        where.extend(self._filters(dsl, "olist", catalog))
        return where

    def _olist_filter_sources(
        self, dsl: QueryDSL, catalog: SemanticCatalog
    ) -> set[str]:
        if catalog.filter_entities:
            sources: set[str] = set()
            for item in dsl.filters:
                sources.update(catalog.filter_entities.get(item.field.lower(), set()))
            return sources
        field_sources = {
            "product_category": {"OrderItem", "Product"},
            "product_category_name": {"OrderItem", "Product"},
            "customer_state": {"Customer"},
            "seller_state": {"OrderItem", "Seller"},
            "payment_type": {"Payment"},
            "order_status": {"Order"},
            "seller": {"OrderItem"},
            "product": {"OrderItem"},
        }
        sources: set[str] = set()
        for item in dsl.filters:
            sources.update(field_sources.get(item.field.lower(), set()))
        return sources

    @staticmethod
    def _olist_from_clause(
        required_sources: set[str], catalog: SemanticCatalog
    ) -> str:
        sources = set(required_sources)
        if sources & {"Product", "Seller"}:
            sources.add("OrderItem")

        entities = catalog.entities
        relationships = {
            item["to_entity"]: item["join"] for item in catalog.relationships
        }

        def source(entity_name: str) -> str:
            entity = entities[entity_name]
            return entity.get("from_sql") or f"{entity['table']} {entity['alias']}"

        tables = source("Order")
        if "OrderItem" in sources:
            tables += f" JOIN {source('OrderItem')} ON {relationships['OrderItem']}"
        if "Customer" in sources:
            tables += f" JOIN {source('Customer')} ON {relationships['Customer']}"
        if "Product" in sources:
            tables += f" JOIN {source('Product')} ON {relationships['Product']}"
        if "Seller" in sources:
            tables += f" JOIN {source('Seller')} ON {relationships['Seller']}"
        if "Payment" in sources:
            tables += f" JOIN {source('Payment')} ON {relationships['Payment']}"
        if "Review" in sources:
            tables += f" JOIN {source('Review')} ON {relationships['Review']}"
        return tables

    def _compile_dw(self, dsl: QueryDSL, metrics: list[MetricDefinition], dims: list[DimensionDefinition]) -> str:
        select_parts = [f"{d.expression} AS {d.name}" for d in dims]
        select_parts.extend(f"{m.expression} AS {m.name}" for m in metrics)
        tables = "fact_order o"
        aliases = {"fact_order"}
        joins = {
            "dim_region": "JOIN dim_region r ON o.region_id = r.region_id",
            "dim_product": "JOIN dim_product p ON o.product_id = p.product_id",
            "dim_customer": "JOIN dim_customer c ON o.customer_id = c.customer_id",
            "dim_date": "JOIN dim_date d ON o.date_id = d.date_id",
        }
        if dsl.time_range:
            tables += " " + joins["dim_date"]
            aliases.add("dim_date")
        for dim in dims:
            if dim.source_table not in aliases:
                tables += " " + joins[dim.source_table]
                aliases.add(dim.source_table)
        where = ["1=1"]
        if dsl.time_range:
            if dsl.time_range.type == "absolute":
                where.append(f"d.date_id >= DATE_FORMAT('{dsl.time_range.start}', '%Y%m%d') AND d.date_id < DATE_FORMAT('{dsl.time_range.end}', '%Y%m%d')")
            else:
                where.append(f"d.date_id >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL {dsl.time_range.value} {dsl.time_range.unit.upper()}), '%Y%m%d')")
        where.extend(self._filters(dsl, "dw"))
        group = f" GROUP BY {', '.join(d.name for d in dims)}" if dims else ""
        return f"SELECT {', '.join(select_parts)} FROM {tables} WHERE {' AND '.join(where)}{group}{self._order_by(dsl, metrics, dims)} LIMIT {dsl.limit}"

    def _filters(
        self,
        dsl: QueryDSL,
        dataset: str,
        catalog: SemanticCatalog | None = None,
    ) -> list[str]:
        expressions = {
            "olist": {"product_category": "p.product_category_name", "customer_state": "c.customer_state", "seller_state": "s.seller_state", "payment_type": "pay.payment_type", "order_status": "o.order_status", "seller": "i.seller_id", "product": "i.product_id"},
            "dw": {"region": "r.region_name", "province": "r.province", "category": "p.category", "product": "p.product_name", "member_level": "c.member_level", "date": "d.date_id"},
        }[dataset]
        if dataset == "olist" and catalog and catalog.filter_expressions:
            expressions = {**expressions, **catalog.filter_expressions}
        output = []
        for item in dsl.filters:
            field = expressions.get(item.field.lower())
            if not field:
                field = item.field
            value = item.value
            if item.operator == FilterOperator.in_:
                values = ", ".join(self._literal(v) for v in value)
                output.append(f"{field} IN ({values})")
            elif item.operator == FilterOperator.between:
                output.append(f"{field} BETWEEN {self._literal(value[0])} AND {self._literal(value[1])}")
            else:
                op = {FilterOperator.eq: "=", FilterOperator.neq: "<>", FilterOperator.contains: "LIKE", FilterOperator.gt: ">", FilterOperator.gte: ">=", FilterOperator.lt: "<", FilterOperator.lte: "<="}[item.operator]
                literal = self._literal(f"%{value}%") if item.operator == FilterOperator.contains else self._literal(value)
                output.append(f"{field} {op} {literal}")
        return output

    @staticmethod
    def _literal(value) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _order_by(dsl: QueryDSL, metrics: list[MetricDefinition], dimensions: list[DimensionDefinition]) -> str:
        if not dsl.order_by:
            return ""
        allowed = {m.name for m in metrics} | {d.name for d in dimensions}
        unknown = [x.field for x in dsl.order_by if x.field.lower() not in {v.lower() for v in allowed}]
        if unknown:
            raise ValueError(f"order_by field is not selected: {unknown}")
        return " ORDER BY " + ", ".join(f"{x.field} {x.direction.upper()}" for x in dsl.order_by)
