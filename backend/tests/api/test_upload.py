"""CSV upload: size/type limits, hostile filenames and malicious cells."""

from __future__ import annotations

import pytest

GOOD = b"Date,INFY,TCS\r\n2024-01-01,100,200\r\n2024-01-02,101,201\r\n2024-01-03,102,199\r\n"


def _up(c, data: bytes, name: str = "prices.csv", ctype: str = "text/csv"):
    return c.post("/api/data/upload", files={"file": (name, data, ctype)})


def test_valid_upload(client):
    r = _up(client, GOOD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataset_id"].startswith("upload-") and body["n_symbols"] == 2


@pytest.mark.parametrize("name", ["../../evil.csv", "..\\..\\evil.csv", "C:\\Windows\\evil.csv", "a/b/c.csv", "<script>.csv", "x\x00.csv"])
def test_hostile_filenames_are_neutralised(client, name):
    r = _up(client, GOOD, name=name)
    assert r.status_code in (200, 415), r.text
    assert "Windows" not in r.text and ".." not in r.text and "<script>" not in r.text


def test_too_large_is_413(client):
    big = GOOD + b"2025-01-01,1,1\r\n" * (6 * 1024 * 1024 // 16)
    assert len(big) > 5 * 1024 * 1024
    r = _up(client, big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize("name", ["prices.xlsx", "prices.csv.exe", "prices", "prices.html"])
def test_non_csv_is_415(client, name):
    r = _up(client, GOOD, name=name)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file_type"


@pytest.mark.parametrize("data,code", [
    (b"", "empty_file"),
    (b"   \r\n", "empty_file"),
    (b"Date,INFY,TCS\r\n2024-01-01,=1+1,200\r\n2024-01-02,101,201\r\n", "invalid_price"),
    (b"Date,INFY,TCS\r\n2024-01-01,@SUM(A1),200\r\n2024-01-02,101,201\r\n", "invalid_price"),
    (b"Date,INFY,TCS\r\n2024-01-01,NaN,200\r\n2024-01-02,101,201\r\n", "invalid_price"),
    (b"Date,INFY,TCS\r\n2024-01-01,inf,200\r\n2024-01-02,101,201\r\n", "invalid_price"),
    (b"Date,INFY,TCS\r\n2024-01-01,-5,200\r\n2024-01-02,101,201\r\n", "invalid_price"),
    (b"Date,=CMD|' /C calc'!A0,TCS\r\n2024-01-01,1,200\r\n2024-01-02,101,201\r\n", "invalid_symbol"),
    (b"Date,INFY,TCS\r\n2024-01-02,1,200\r\n2024-01-01,101,201\r\n", "invalid_date"),
    (b"Date,INFY,TCS\r\nyesterday,1,200\r\n2024-01-01,101,201\r\n", "invalid_date"),
    (b"\xff\xfe\x00b\x00a\x00d", "invalid_encoding"),
    (b"Symbol\r\nINFY\r\n", "invalid_csv"),
])
def test_malicious_or_bad_content_is_422(client, data, code):
    r = _up(client, data)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code


def test_upload_requires_multipart_file(client):
    assert client.post("/api/data/upload", json={"path": "C:\\data.csv"}).status_code == 422
    assert client.post("/api/data/upload", data={"url": "http://example.com/x.csv"}).status_code == 422
