"""电商语义目录和 DSL 业务校验。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.enterprise.dsl import QueryDSL


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    expression: str
    source_table: str
    time_field: str
    aliases: tuple[str, ...] = ()
    allowed_dimensions: tuple[str, ...] = ()
    default_filter: str | None = None
    description: str = ""
    source_entity: str | None = None
    family: str | None = None


@dataclass(frozen=True)
class DimensionDefinition:
    name: str
    expression: str
    source_table: str
    aliases: tuple[str, ...] = ()
    source_entity: str | None = None


@dataclass
class SemanticCatalog:
    dataset: str
    metrics: dict[str, MetricDefinition]
    dimensions: dict[str, DimensionDefinition]
    tables: set[str]
    joins: tuple[str, ...] = ()
    enum_values: dict[str, set[str]] = field(default_factory=dict)
    filter_fields: frozenset[str] = frozenset()
    entities: dict[str, dict] = field(default_factory=dict)
    relationships: tuple[dict, ...] = ()
    filter_expressions: dict[str, str] = field(default_factory=dict)
    filter_entities: dict[str, frozenset[str]] = field(default_factory=dict)

    def metric(self, name: str) -> MetricDefinition:
        key = name.lower()
        if key not in self.metrics:
            raise ValueError(f"unknown metric: {name}")
        return self.metrics[key]

    def dimension(self, name: str) -> DimensionDefinition:
        key = name.lower()
        if key not in self.dimensions:
            raise ValueError(f"unknown dimension: {name}")
        return self.dimensions[key]

    def validate(self, dsl: QueryDSL) -> list[str]:
        errors: list[str] = []
        if dsl.dataset != self.dataset:
            errors.append(f"dataset {dsl.dataset!r} does not match catalog {self.dataset!r}")
            return errors
        metric_defs: list[MetricDefinition] = []
        for name in dsl.metrics:
            try:
                metric_defs.append(self.metric(name))
            except ValueError as exc:
                errors.append(str(exc))
        for name in dsl.dimensions:
            try:
                dim = self.dimension(name)
                for metric in metric_defs:
                    if metric.allowed_dimensions and dim.name not in metric.allowed_dimensions:
                        errors.append(f"dimension {dim.name} is not allowed for metric {metric.name}")
            except ValueError as exc:
                errors.append(str(exc))
        if not dsl.time_range:
            errors.append("time_range is required for governed analytical queries")
        for f in dsl.filters:
            field_name = f.field.lower()
            if (
                field_name not in self.dimensions
                and field_name not in self.filter_fields
                and field_name not in self.enum_values
            ):
                errors.append(f"unknown filter field: {f.field}")
            values = f.value if isinstance(f.value, list) else [f.value]
            allowed = self.enum_values.get(field_name)
            if allowed and any(str(v) not in allowed for v in values):
                errors.append(f"invalid value for {f.field}: {values}")
        valid_order_fields = set(self.metrics) | set(self.dimensions)
        for item in dsl.order_by:
            if item.field.lower() not in valid_order_fields:
                errors.append(f"unknown order_by field: {item.field}")
        if dsl.comparison and not dsl.time_range:
            errors.append("comparison requires time_range")
        return errors


def olist_catalog() -> SemanticCatalog:
    from app.enterprise.ontology import load_ontology_catalog

    return load_ontology_catalog("olist")


def dw_catalog() -> SemanticCatalog:
    dims = {
        "region": DimensionDefinition("region", "r.region_name", "dim_region", ("地区", "区域")),
        "province": DimensionDefinition("province", "r.province", "dim_region", ("省份",)),
        "category": DimensionDefinition("category", "p.category", "dim_product", ("品类", "类目")),
        "product": DimensionDefinition("product", "p.product_name", "dim_product", ("商品",)),
        "member_level": DimensionDefinition("member_level", "c.member_level", "dim_customer", ("会员等级",)),
        "date": DimensionDefinition("date", "d.date_id", "dim_date", ("日期",)),
    }
    all_dims = tuple(dims)
    metrics = {
        "gmv": MetricDefinition("gmv", "SUM(o.order_amount)", "fact_order", "d.date_id", ("销售额", "成交总额", "GMV"), all_dims),
        "order_count": MetricDefinition("order_count", "COUNT(DISTINCT o.order_id)", "fact_order", "d.date_id", ("订单数",), all_dims),
        "item_count": MetricDefinition("item_count", "SUM(o.order_quantity)", "fact_order", "d.date_id", ("销量", "件数"), all_dims),
        "aov": MetricDefinition("aov", "SUM(o.order_amount) / NULLIF(COUNT(DISTINCT o.order_id), 0)", "fact_order", "d.date_id", ("客单价",), all_dims),
    }
    return SemanticCatalog("dw", metrics, dims, {"fact_order", "dim_region", "dim_product", "dim_customer", "dim_date"}, joins=("o.region_id=r.region_id", "o.product_id=p.product_id", "o.customer_id=c.customer_id", "o.date_id=d.date_id"), filter_fields=frozenset({"region", "province", "category", "product", "member_level", "date"}))


def get_catalog(dataset: str) -> SemanticCatalog:
    if dataset == "olist":
        return olist_catalog()
    if dataset == "dw":
        return dw_catalog()
    raise ValueError(f"unsupported dataset: {dataset}")
