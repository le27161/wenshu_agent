"""Import the public Olist CSV archive into a normalized MySQL schema.

The dataset stays outside the repository. Set OLIST_DATA_DIR to the archive
directory and run this module after the MySQL container is healthy.
"""

from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path

import asyncmy

from app.conf.app_config import app_config


TABLES = {
    "olist_orders_dataset.csv": ("fact_order", """CREATE TABLE IF NOT EXISTS fact_order (
        order_id VARCHAR(64) PRIMARY KEY, customer_id VARCHAR(64) NOT NULL,
        order_status VARCHAR(32) NOT NULL, order_purchase_timestamp DATETIME,
        order_approved_at DATETIME NULL, order_delivered_carrier_date DATETIME NULL,
        order_delivered_customer_date DATETIME NULL, order_estimated_delivery_date DATETIME NULL
    )""", ["order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"]),
    "olist_order_items_dataset.csv": ("fact_order_item", """CREATE TABLE IF NOT EXISTS fact_order_item (
        order_id VARCHAR(64) NOT NULL, order_item_id INT NOT NULL, product_id VARCHAR(64) NOT NULL,
        seller_id VARCHAR(64) NOT NULL, shipping_limit_date DATETIME, price DECIMAL(12,2),
        freight_value DECIMAL(12,2), PRIMARY KEY(order_id, order_item_id)
    )""", ["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price", "freight_value"]),
    "olist_order_payments_dataset.csv": ("fact_payment", """CREATE TABLE IF NOT EXISTS fact_payment (
        order_id VARCHAR(64) NOT NULL, payment_sequential INT NOT NULL, payment_type VARCHAR(32),
        payment_installments INT, payment_value DECIMAL(12,2), PRIMARY KEY(order_id, payment_sequential)
    )""", ["order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"]),
    "olist_order_reviews_dataset.csv": ("fact_review", """CREATE TABLE IF NOT EXISTS fact_review (
        review_id VARCHAR(64) NOT NULL, order_id VARCHAR(64) NOT NULL, review_score INT,
        review_comment_title TEXT, review_comment_message TEXT, review_creation_date DATETIME,
        review_answer_timestamp DATETIME, PRIMARY KEY(review_id, order_id)
    )""", ["review_id", "order_id", "review_score", "review_comment_title", "review_comment_message", "review_creation_date", "review_answer_timestamp"]),
    "olist_customers_dataset.csv": ("dim_customer", """CREATE TABLE IF NOT EXISTS dim_customer (
        customer_id VARCHAR(64) PRIMARY KEY, customer_unique_id VARCHAR(64),
        customer_zip_code_prefix VARCHAR(16), customer_city VARCHAR(128), customer_state VARCHAR(8)
    )""", ["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state"]),
    "olist_products_dataset.csv": ("dim_product", """CREATE TABLE IF NOT EXISTS dim_product (
        product_id VARCHAR(64) PRIMARY KEY, product_category_name VARCHAR(128), product_name_length INT,
        product_description_length INT, product_photos_qty INT, product_weight_g DECIMAL(12,2),
        product_length_cm DECIMAL(12,2), product_height_cm DECIMAL(12,2), product_width_cm DECIMAL(12,2)
    )""", ["product_id", "product_category_name", "product_name_length", "product_description_length", "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]),
    "olist_sellers_dataset.csv": ("dim_seller", """CREATE TABLE IF NOT EXISTS dim_seller (
        seller_id VARCHAR(64) PRIMARY KEY, seller_zip_code_prefix VARCHAR(16), seller_city VARCHAR(128), seller_state VARCHAR(8)
    )""", ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"]),
    "olist_geolocation_dataset.csv": ("dim_geography", """CREATE TABLE IF NOT EXISTS dim_geography (
        geolocation_zip_code_prefix VARCHAR(16), geolocation_lat DECIMAL(12,8), geolocation_lng DECIMAL(12,8),
        geolocation_city VARCHAR(128), geolocation_state VARCHAR(8), geolocation_id BIGINT AUTO_INCREMENT PRIMARY KEY,
        KEY idx_geolocation_zip_city_state (geolocation_zip_code_prefix, geolocation_city, geolocation_state)
    )""", ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng", "geolocation_city", "geolocation_state"]),
}

TRANSLATION_DDL = """CREATE TABLE IF NOT EXISTS dim_product_category_translation (
    product_category_name VARCHAR(128) PRIMARY KEY,
    product_category_name_english VARCHAR(128)
)"""


def _clean(value: str | None):
    return None if value in (None, "") else value


async def import_olist(data_dir: Path) -> dict:
    # The database may not exist on a fresh Compose volume. Create it through
    # a server-level connection before opening the dataset connection.
    server = await asyncmy.connect(
        host=app_config.db_dw.host,
        port=app_config.db_dw.port,
        user=app_config.db_dw.user,
        password=app_config.db_dw.password,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        async with server.cursor() as cursor:
            database = app_config.db_olist.database.replace("`", "``")
            await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
    finally:
        server.close()

    connection = await asyncmy.connect(
        host=app_config.db_olist.host,
        port=app_config.db_olist.port,
        user=app_config.db_olist.user,
        password=app_config.db_olist.password,
        database=app_config.db_olist.database,
        autocommit=False,
        charset="utf8mb4",
    )
    try:
        async with connection.cursor() as cursor:
            for filename, (table, ddl, columns) in TABLES.items():
                source = data_dir / filename
                if not source.exists():
                    raise FileNotFoundError(source)
                await cursor.execute(f"DROP TABLE IF EXISTS {table}")
                await cursor.execute(ddl)
                placeholders = ",".join(["%s"] * len(columns))
                column_sql = ",".join(f"`{c}`" for c in columns)
                sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"
                batch: list[tuple] = []
                source_columns = [
                    {
                        "product_name_length": "product_name_lenght",
                        "product_description_length": "product_description_lenght",
                    }.get(column, column)
                    for column in columns
                ]
                with source.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        # The public archive keeps two historical misspellings
                        # in the products header; normalize them to our schema.
                        batch.append(tuple(_clean(row.get(source_name)) for source_name in source_columns))
                        if len(batch) >= 1000:
                            await cursor.executemany(sql, batch)
                            batch.clear()
                if batch:
                    await cursor.executemany(sql, batch)
                print(f"imported {table} from {filename}")
            translation_source = data_dir / "product_category_name_translation.csv"
            if translation_source.exists():
                await cursor.execute("DROP TABLE IF EXISTS dim_product_category_translation")
                await cursor.execute(TRANSLATION_DDL)
                rows = []
                with translation_source.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        rows.append((_clean(row.get("product_category_name")), _clean(row.get("product_category_name_english"))))
                if rows:
                    await cursor.executemany(
                        "INSERT INTO dim_product_category_translation (product_category_name, product_category_name_english) VALUES (%s, %s)",
                        rows,
                    )
                print(f"imported dim_product_category_translation ({len(rows)} rows)")
            row_counts = {}
            for table, _, _ in TABLES.values():
                expected = await _count(cursor, table)
                if expected == 0:
                    raise RuntimeError(f"imported table is empty: {table}")
                row_counts[table] = expected
            checks = {
                "orphan_items": "SELECT COUNT(*) FROM fact_order_item i LEFT JOIN fact_order o ON o.order_id=i.order_id WHERE o.order_id IS NULL",
                "orphan_payments": "SELECT COUNT(*) FROM fact_payment p LEFT JOIN fact_order o ON o.order_id=p.order_id WHERE o.order_id IS NULL",
                "orphan_reviews": "SELECT COUNT(*) FROM fact_review r LEFT JOIN fact_order o ON o.order_id=r.order_id WHERE o.order_id IS NULL",
                "orphan_products": "SELECT COUNT(*) FROM fact_order_item i LEFT JOIN dim_product p ON p.product_id=i.product_id WHERE p.product_id IS NULL",
                "orphan_sellers": "SELECT COUNT(*) FROM fact_order_item i LEFT JOIN dim_seller s ON s.seller_id=i.seller_id WHERE s.seller_id IS NULL",
            }
            check_counts = {}
            for name, sql in checks.items():
                await cursor.execute(sql)
                count = int((await cursor.fetchone())[0])
                if count:
                    raise RuntimeError(f"{name}={count}")
                check_counts[name] = count
        await connection.commit()
        return {"row_counts": row_counts, "foreign_key_checks": check_counts}
    finally:
        connection.close()


async def _count(cursor, table: str) -> int:
    await cursor.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
    row = await cursor.fetchone()
    return int(row[0])


if __name__ == "__main__":
    directory = Path(os.environ.get("OLIST_DATA_DIR", ""))
    if not directory.exists():
        raise SystemExit("Set OLIST_DATA_DIR to the Olist archive directory")
    print(asyncio.run(import_olist(directory)))
