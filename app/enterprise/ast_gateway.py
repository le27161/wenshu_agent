"""SQLGlot-based policy rewriting and security validation gateway."""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.enterprise.policy import AppliedPolicy, PolicyEngine, Principal


@dataclass(frozen=True)
class GatewayResult:
    sql: str
    policy: AppliedPolicy


class ASTPolicyGateway:
    """Parse, rewrite, and validate SQL before it reaches the read-only database."""

    def __init__(self, policy_engine: PolicyEngine | None = None, max_joins: int = 8, max_subquery_depth: int = 3):
        self.policy_engine = policy_engine or PolicyEngine()
        self.max_joins = max_joins
        self.max_subquery_depth = max_subquery_depth

    def protect(self, sql: str, principal: Principal, dataset: str) -> GatewayResult:
        policy = self.policy_engine.authorize_dataset(principal, dataset)
        try:
            statements = sqlglot.parse(sql, read="mysql")
        except sqlglot.ParseError as exc:
            raise ValueError(f"SQL AST parse failed: {exc}") from exc
        if len(statements) != 1:
            raise PermissionError("multiple SQL statements are not allowed")
        tree = statements[0]
        self._validate_structure(tree, policy)
        denied: list[str] = []
        masked: list[str] = []
        for column in tree.find_all(exp.Column):
            rule = self.policy_engine.column_rule(principal, column.name, dataset)
            if rule and rule.action == "deny":
                denied.append(column.name)
            elif rule and rule.action == "mask":
                masked.append(column.name)
                if rule.mask_expression:
                    replacement = rule.mask_expression.format(column=column.sql(dialect="mysql"))
                    column.replace(sqlglot.parse_one(replacement, read="mysql"))
        if denied:
            raise PermissionError(f"column access denied: {sorted(set(denied))}")
        predicates = self.policy_engine.row_predicates(principal, dataset)
        if predicates:
            where = tree.args.get("where")
            predicate = sqlglot.parse_one(" AND ".join(predicates), read="mysql")
            if where:
                where.set("this", exp.and_(where.this, predicate))
            else:
                tree.set("where", exp.Where(this=predicate))
        if not tree.args.get("limit"):
            tree.set("limit", exp.Limit(expression=exp.Literal.number(1000)))
        final_sql = tree.sql(dialect="mysql")
        # Re-parse the serialized SQL and verify that policy predicates survive
        # AST rewriting. This prevents a serializer or later transformation
        # from accidentally dropping the mandatory row scope.
        verified = sqlglot.parse_one(final_sql, read="mysql")
        final_text = verified.sql(dialect="mysql").lower()
        for predicate in predicates:
            if predicate.lower().replace(" ", "") not in final_text.replace(" ", ""):
                raise PermissionError("row-level policy predicate missing after AST rewrite")
        return GatewayResult(
            final_sql,
            AppliedPolicy(
                predicates,
                tuple(sorted(set(masked))),
                tuple(sorted(set(denied))),
            ),
        )

    def validate(self, sql: str, principal: Principal, dataset: str) -> None:
        """Validate an already protected statement without injecting policy twice."""
        policy = self.policy_engine.authorize_dataset(principal, dataset)
        try:
            statements = sqlglot.parse(sql, read="mysql")
        except sqlglot.ParseError as exc:
            raise ValueError(f"SQL AST parse failed: {exc}") from exc
        if len(statements) != 1:
            raise PermissionError("multiple SQL statements are not allowed")
        tree = statements[0]
        self._validate_structure(tree, policy)
        for column in tree.find_all(exp.Column):
            rule = self.policy_engine.column_rule(principal, column.name, dataset)
            if rule and rule.action == "deny":
                raise PermissionError(f"column access denied: {column.name}")
        predicates = self.policy_engine.row_predicates(principal, dataset)
        text = tree.sql(dialect="mysql").lower().replace(" ", "")
        for predicate in predicates:
            if predicate.lower().replace(" ", "") not in text:
                raise PermissionError("row-level policy predicate missing")

    def _validate_structure(self, tree: exp.Expression, policy) -> None:
        if not isinstance(tree, exp.Select):
            raise PermissionError("only SELECT or WITH ... SELECT statements are allowed")
        if tree.args.get("with") and not isinstance(tree, exp.Select):
            raise PermissionError("WITH must wrap a SELECT statement")
        forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Transaction)
        if tree.find(forbidden):
            raise PermissionError("DML, DDL, transaction, and command statements are forbidden")
        cte_names = {
            cte.alias_or_name.lower()
            for cte in tree.find_all(exp.CTE)
            if cte.alias_or_name
        }
        if cte_names & {name.lower() for name in policy.allowed_tables}:
            raise PermissionError("CTE name must not shadow governed tables")
        joins = list(tree.find_all(exp.Join))
        if len(joins) > self.max_joins:
            raise PermissionError(f"join count exceeds limit: {self.max_joins}")
        subqueries = list(tree.find_all(exp.Subquery))
        if len(subqueries) > self.max_subquery_depth:
            raise PermissionError("subquery depth exceeds limit")
        for table in tree.find_all(exp.Table):
            if (
                table.name not in policy.allowed_tables
                and table.name.lower() not in cte_names
                and table.name not in {"dual"}
            ):
                raise PermissionError(f"table access denied: {table.name}")
        for column in tree.find_all(exp.Column):
            if column.name == "*":
                continue
            if column.name.lower() not in policy.allowed_columns:
                raise PermissionError(f"column access denied: {column.name}")
        # Check aggregate and user-defined function nodes. Boolean expressions
        # such as AND/OR also inherit from Func in SQLGlot, so they are
        # intentionally excluded from this allow-list check.
        for func in tree.find_all(exp.AggFunc):
            name = func.sql_name().upper()
            if name not in policy.allowed_functions:
                raise PermissionError(f"function access denied: {name}")
        for func in tree.find_all(exp.Anonymous):
            name = func.name.upper()
            if name not in policy.allowed_functions:
                raise PermissionError(f"function access denied: {name}")
        if isinstance(tree, exp.Select) and not tree.args.get("limit") and tree.find(exp.Star):
            raise PermissionError("unbounded SELECT * is not allowed")
        # Every governed query must carry a bounded time predicate. The
        # deterministic compiler emits one of these canonical fields.
        sql_text = tree.sql(dialect="mysql").lower()
        if any(t in sql_text for t in policy.allowed_tables if t.startswith("fact_")):
            if not any(token in sql_text for token in ("order_purchase_timestamp", "date_id")):
                raise PermissionError("fact table query requires a time range")
