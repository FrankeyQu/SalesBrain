from __future__ import annotations

import pytest

from salesbrain.eboss import EbossClient


def test_build_params_and_extract_records():
    client = EbossClient("http://example.com/api", "secret")

    params = client.build_params("get-user-by-name", {"realName": "Alice"})
    assert params["realName"] == "Alice"
    assert params["current"] == "1"
    assert params["size"] == "10"
    assert params["status"] == "1"

    with pytest.raises(ValueError):
        client.build_params("get-user-by-name", {"unexpected": "value"})

    assert EbossClient.extract_records({"data": {"records": [{"id": 1, "name": "Project A"}]}}) == [
        {"id": 1, "name": "Project A"}
    ]
    assert EbossClient.extract_records({"data": [{"id": 2, "name": "Lead A"}]}) == [
        {"id": 2, "name": "Lead A"}
    ]
    assert EbossClient.extract_object_summary({"projectId": 7, "projectName": "Alpha"}) == ("7", "Alpha")
