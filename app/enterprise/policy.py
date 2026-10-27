"""Mock RBAC and row/column policy definitions used by the query gateway."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    user_id: str = "demo-user"
    tenant_id: str = "demo-tenant"
    roles: tuple[str, ...] = ("admin",)
    store_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()
    allowed_datasets: tuple[str, ...] = ("dw", "olist")


@dataclass(frozen=True)
class ColumnRule:
    action: str
    mask_expression: str | None = None


@dataclass(frozen=True)
class DatasetPolicy:
    allowed_tables: frozenset[str]
    allowed_functions: frozenset[str]
    row_column: str | None = None
    column_rules: dict[str, ColumnRule] = field(default_factory=dict)
    allowed_columns: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AppliedPolicy:
    row_predicates: tuple[str, ...]
    masked_columns: tuple[str, ...]
    denied_columns: tuple[str, ...]


class PolicyEngine:
    def __init__(self):
        self.policies = {
            "olist": DatasetPolicy(
                frozenset({"fact_order", "fact_order_item", "fact_payment", "fact_review", "dim_customer", "dim_product", "dim_seller", "dim_geography"}),
                frozenset({"SUM", "COUNT", "AVG", "MAX", "MIN", "NULLIF", "CASE", "DATE", "DATEDIFF", "DATE_SUB", "CURDATE", "CONCAT", "LEFT", "RIGHT"}),
                column_rules={
                    "customer_name": ColumnRule("mask", "CONCAT(LEFT({column}, 1), '*')"),
                    "customer_phone": ColumnRule("deny"),
                    "customer_address": ColumnRule("deny"),
                    "cost_price": ColumnRule("deny"),
                    "seller_settlement_amount": ColumnRule("deny"),
                },
                allowed_columns=frozenset({
                    "order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date", "gmv", "order_count", "item_count", "aov", "payment_value", "delivery_days", "review_score", "low_review_rate", "cancel_rate", "seller_order_count", "product_category", "seller", "seller_state", "customer_state", "payment_type", "order_date", "product",
                    "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price", "freight_value", "payment_sequential", "payment_type", "payment_installments", "payment_value", "review_id", "review_score", "review_comment_title", "review_comment_message", "review_creation_date", "review_answer_timestamp", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state", "product_category_name", "product_name_length", "product_description_length", "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm", "seller_zip_code_prefix", "seller_city", "seller_state", "geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng", "geolocation_city", "geolocation_state",
                }),
            ),
            "dw": DatasetPolicy(
                frozenset({"fact_order", "dim_region", "dim_product", "dim_customer", "dim_date"}),
                frozenset({"SUM", "COUNT", "AVG", "MAX", "MIN", "NULLIF", "CASE", "DATE_FORMAT", "DATE_SUB", "CURDATE", "CONCAT", "LEFT", "RIGHT"}),
                column_rules={"customer_name": ColumnRule("mask", "CONCAT(LEFT({column}, 1), '*')"), "cost_price": ColumnRule("deny")},
                allowed_columns=frozenset({"order_id", "region_id", "product_id", "customer_id", "date_id", "order_quantity", "order_amount", "region_name", "province", "country", "product_name", "category", "brand", "member_level", "date", "year", "quarter", "month", "day", "customer_name", "gender", "gmv", "order_count", "item_count", "aov", "region", "province", "category", "product", "member_level"}),
            ),
        }

    def authorize_dataset(self, principal: Principal, dataset: str) -> DatasetPolicy:
        if dataset not in principal.allowed_datasets:
            raise PermissionError(f"dataset access denied: {dataset}")
        try:
            return self.policies[dataset]
        except KeyError as exc:
            raise PermissionError(f"unsupported dataset: {dataset}") from exc

    def row_predicates(self, principal: Principal, dataset: str) -> tuple[str, ...]:
        if "admin" in principal.roles:
            return ()
        if dataset == "olist":
            predicates = []
            if principal.region_ids:
                values = ", ".join(self._literal(v) for v in principal.region_ids)
                predicates.append(f"o.customer_id IN (SELECT customer_id FROM dim_customer WHERE customer_state IN ({values}))")
            if principal.store_ids:
                values = ", ".join(self._literal(v) for v in principal.store_ids)
                predicates.append(f"i.seller_id IN ({values})")
            if predicates:
                return tuple(predicates)
        if dataset == "dw" and principal.region_ids:
            values = ", ".join(self._literal(v) for v in principal.region_ids)
            return (f"o.region_id IN (SELECT region_id FROM dim_region WHERE region_name IN ({values}))",)
        raise PermissionError("principal has no row-level scope for this dataset")

    def column_rule(self, principal: Principal, column: str, dataset: str) -> ColumnRule | None:
        policy = self.authorize_dataset(principal, dataset)
        if "admin" in principal.roles:
            return ColumnRule("allow")
        return policy.column_rules.get(column.lower())

    @staticmethod
    def _literal(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"
