import json

import pytest

from orders_pipeline.operations import extract, extract_fixture, load, transform


@pytest.mark.parametrize(
    ("fixture", "result"), [("official", "1236.70"), ("changed", "60.00")]
)
def test_complete_workflow(fixture, result):
    assert load(transform(extract_fixture(fixture)))["total_order_value"] == result


def test_invalid_input_causes_real_failure():
    with pytest.raises(ValueError, match="Invalid amount"):
        transform(extract_fixture("invalid"))


def test_extract_rejects_non_object_data(tmp_path):
    source = tmp_path / "orders.json"
    source.write_text(json.dumps([301.27, 433.21]), encoding="utf8")
    with pytest.raises(ValueError, match="Expected object"):
        extract(source)


@pytest.mark.parametrize("selection", ["missing", "../official"])
def test_unknown_fixture_is_rejected(selection):
    with pytest.raises(ValueError, match="Unknown fixture"):
        extract_fixture(selection)


@pytest.mark.parametrize(
    "orders",
    [{}, [], {"1001": True}, {"1001": None}, {"1001": "NaN"}, {"1001": "Infinity"}],
)
def test_transform_rejects_invalid_orders(orders):
    with pytest.raises(ValueError):
        transform(orders)
