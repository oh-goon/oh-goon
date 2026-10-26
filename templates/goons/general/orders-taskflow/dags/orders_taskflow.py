"""Airflow 3 TaskFlow adapter to the exact same ordinary Python operations."""

import os

import pendulum
from airflow.sdk import dag, task
from orders_pipeline.operations import extract_fixture, load, transform


@dag(
    dag_id="orders_taskflow_comparison",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
)
def orders_taskflow_comparison():
    @task
    def extract_orders():
        return extract_fixture(os.environ.get("ORDERS_FIXTURE", "official"))

    @task
    def transform_orders(orders):
        return transform(orders)

    @task
    def load_orders(total):
        result = load(total)
        print("Total order value is:", result["total_order_value"])
        return result

    load_orders(transform_orders(extract_orders()))


orders_taskflow_comparison()
