import unittest

from app.enterprise.dsl import AnalysisIntent, DSLFilter, QueryDSL, TimeRange
from app.enterprise.ontology import load_ontology_catalog


class OntologyCatalogTest(unittest.TestCase):
    def test_olist_ontology_loads_as_semantic_catalog(self):
        catalog = load_ontology_catalog("olist")

        self.assertEqual(catalog.dataset, "olist")
        self.assertIn("Order", catalog.entities)
        self.assertIn("OrderItem", catalog.entities)
        self.assertIn("Payment", catalog.entities)
        self.assertIn("Review", catalog.entities)
        self.assertIn("gmv", catalog.metrics)
        self.assertIn("payment_value", catalog.metrics)
        self.assertIn("product_category", catalog.dimensions)
        self.assertIn("order_payment", {item["name"] for item in catalog.relationships})

    def test_olist_ontology_exposes_filter_expressions_and_sources(self):
        catalog = load_ontology_catalog("olist")

        self.assertEqual(
            catalog.filter_expressions["product_category"],
            "p.product_category_name",
        )
        self.assertEqual(
            catalog.filter_entities["product_category"],
            frozenset({"OrderItem", "Product"}),
        )
        self.assertEqual(catalog.filter_expressions["payment_type"], "pay.payment_type")
        self.assertEqual(catalog.filter_entities["payment_type"], frozenset({"Payment"}))

    def test_representative_olist_metrics_dimensions_and_filters_validate(self):
        catalog = load_ontology_catalog("olist")
        dsl = QueryDSL(
            dataset="olist",
            intent=AnalysisIntent.aggregate,
            metrics=[
                "gmv",
                "order_count",
                "payment_value",
                "review_score",
                "low_review_rate",
                "cancel_rate",
                "delivery_days",
            ],
            dimensions=list(catalog.dimensions),
            filters=[
                DSLFilter(field="order_status", operator="eq", value="delivered"),
                DSLFilter(field="payment_type", operator="eq", value="credit_card"),
                DSLFilter(field="product_category", operator="eq", value="beleza_saude"),
            ],
            time_range=TimeRange(type="absolute", start="2017-01-01", end="2018-01-01"),
        )

        self.assertEqual(catalog.validate(dsl), [])

        seller_dsl = QueryDSL(
            dataset="olist",
            intent=AnalysisIntent.aggregate,
            metrics=["seller_order_count"],
            dimensions=["seller", "seller_state", "product_category"],
            time_range=TimeRange(type="absolute", start="2017-01-01", end="2018-01-01"),
        )

        self.assertEqual(catalog.validate(seller_dsl), [])

    def test_rejects_unknown_metric_dimension_and_invalid_enum(self):
        catalog = load_ontology_catalog("olist")
        dsl = QueryDSL(
            dataset="olist",
            intent=AnalysisIntent.aggregate,
            metrics=["unknown_metric"],
            dimensions=["unknown_dimension"],
            filters=[
                DSLFilter(field="order_status", operator="eq", value="not_a_status")
            ],
            time_range=TimeRange(type="absolute", start="2017-01-01", end="2018-01-01"),
        )

        errors = catalog.validate(dsl)

        self.assertTrue(any("unknown metric" in item for item in errors))
        self.assertTrue(any("unknown dimension" in item for item in errors))
        self.assertTrue(any("invalid value for order_status" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
