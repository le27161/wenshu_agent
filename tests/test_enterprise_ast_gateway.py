import importlib.util
import unittest

SQLGLOT_AVAILABLE = importlib.util.find_spec("sqlglot") is not None

if SQLGLOT_AVAILABLE:
    from app.enterprise.ast_gateway import ASTPolicyGateway
    from app.enterprise.policy import Principal

    ADMIN = Principal(roles=("admin",), allowed_datasets=("dw", "olist"))
else:
    ASTPolicyGateway = None
    Principal = None
    ADMIN = None


@unittest.skipUnless(SQLGLOT_AVAILABLE, "sqlglot is not installed")
class ASTPolicyGatewayTest(unittest.TestCase):
    def test_allows_cte_with_having_and_order_by(self):
        sql = """
        WITH regional AS (
          SELECT o.region_id, SUM(o.order_amount) AS gmv
          FROM fact_order o
          WHERE o.date_id >= 20250101
          GROUP BY o.region_id
          HAVING SUM(o.order_amount) > 100
        )
        SELECT regional.region_id, regional.gmv
        FROM regional
        ORDER BY regional.gmv DESC
        LIMIT 10
        """

        result = ASTPolicyGateway().protect(sql, ADMIN, "dw")

        self.assertIn("WITH", result.sql.upper())
        self.assertIn("ORDER BY", result.sql.upper())

    def test_rejects_cte_shadowing_governed_table(self):
        sql = """
        WITH fact_order AS (
          SELECT 1 AS order_id, 20250101 AS date_id
        )
        SELECT order_id FROM fact_order WHERE date_id >= 20250101
        """

        with self.assertRaisesRegex(PermissionError, "shadow"):
            ASTPolicyGateway().protect(sql, ADMIN, "dw")

    def test_rejects_forbidden_dml_in_subquery(self):
        with self.assertRaises(PermissionError):
            ASTPolicyGateway().protect(
                "SELECT order_id FROM fact_order WHERE date_id >= 20250101; DELETE FROM fact_order",
                ADMIN,
                "dw",
            )

    def test_non_admin_requires_row_scope_after_rewrite(self):
        principal = Principal(
            roles=("analyst",),
            region_ids=("华东",),
            allowed_datasets=("dw",),
        )

        result = ASTPolicyGateway().protect(
            "SELECT SUM(o.order_amount) AS gmv FROM fact_order o WHERE o.date_id >= 20250101",
            principal,
            "dw",
        )

        self.assertIn("dim_region", result.sql)
        self.assertTrue(result.policy.row_predicates)


if __name__ == "__main__":
    unittest.main()
