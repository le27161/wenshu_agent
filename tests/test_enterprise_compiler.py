import unittest

from app.enterprise.compiler import DSLCompiler
from app.enterprise.dsl import AnalysisIntent, OrderBy, QueryDSL, TimeRange
from app.enterprise.semantic import get_catalog


class DSLCompilerTest(unittest.TestCase):
    def test_olist_mixed_fact_metrics_compile_to_ctes(self):
        dsl = QueryDSL(
            dataset="olist",
            intent=AnalysisIntent.aggregate,
            metrics=["gmv", "payment_value", "review_score"],
            dimensions=["product_category"],
            time_range=TimeRange(
                type="absolute", start="2017-01-01", end="2018-01-01"
            ),
            order_by=[OrderBy(field="gmv", direction="desc")],
            limit=20,
        )

        compiled = DSLCompiler().compile(dsl, get_catalog("olist"))

        self.assertTrue(compiled.sql.startswith("WITH "))
        self.assertIn("fact_payment", compiled.sql)
        self.assertIn("fact_review", compiled.sql)
        self.assertIn("product_category", compiled.sql)
        self.assertIn("ORDER BY gmv DESC", compiled.sql)
        self.assertIn("entities", compiled.explanation)
        self.assertIn("relationships", compiled.explanation)
        self.assertIn("metric_definitions", compiled.explanation)
        self.assertEqual(
            {"gmv", "payment_value", "review_score"},
            {item["name"] for item in compiled.explanation["metric_definitions"]},
        )

    def test_dw_order_by_must_be_selected(self):
        dsl = QueryDSL(
            dataset="dw",
            intent=AnalysisIntent.aggregate,
            metrics=["gmv"],
            dimensions=["region"],
            time_range=TimeRange(
                type="absolute", start="2025-01-01", end="2025-04-01"
            ),
            order_by=[OrderBy(field="order_count", direction="desc")],
        )

        with self.assertRaisesRegex(ValueError, "order_by field is not selected"):
            DSLCompiler().compile(dsl, get_catalog("dw"))

    def test_olist_preserves_dsl_metric_order_across_ctes(self):
        dsl = QueryDSL(
            dataset="olist",
            intent=AnalysisIntent.aggregate,
            metrics=["gmv", "order_count", "aov"],
            dimensions=["product_category"],
            time_range=TimeRange(
                type="absolute", start="2018-01-01", end="2018-09-01"
            ),
            order_by=[OrderBy(field="gmv", direction="desc")],
            limit=20,
        )

        compiled = DSLCompiler().compile(dsl, get_catalog("olist"))

        self.assertIn(
            "SELECT metric_0.product_category AS product_category, "
            "metric_0.gmv AS gmv, "
            "metric_1.order_count AS order_count, "
            "metric_0.aov AS aov",
            compiled.sql,
        )


if __name__ == "__main__":
    unittest.main()
