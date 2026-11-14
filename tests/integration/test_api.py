from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient

from tz_auditor import api


@pytest.fixture
def client():
    with TestClient(api.app) as client:
        yield client


def test_endpoints(client, synthetic_dir):
    assert client.get("/health").json() == {"status": "ok"}
    assert "<form" in client.get("/").text
    for name, count in [("shelves.docx", 0), ("shelves_conflict.docx", 1)]:
        data = (synthetic_dir / name).read_bytes()
        response = client.post("/audit", files={"file": (name, data)})
        assert response.status_code == 200
        assert len(response.json()["findings"]) == count
        assert response.json()["filename"] == name
        assert response.json() == client.post("/audit", files={"file": (name, data)}).json()


def test_bad_uploads(client):
    assert client.post("/audit").status_code == 422
    assert client.post("/audit", files={"file": ("bad.txt", b"hello")}).status_code == 415
    assert client.post("/audit", files={"file": ("bad.docx", b"hello")}).status_code == 422
    assert client.post("/audit", files={"file": ("empty.docx", b"")}).status_code == 422


def test_size_limits(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD", 5)
    assert client.post("/audit", files={"file": ("large.docx", b"x" * 6)}).status_code == 413
    monkeypatch.setattr(api, "MAX_BODY", 10)
    assert (
        client.post(
            "/audit",
            content=b"x" * 11,
            headers={"content-type": "multipart/form-data; boundary=abc"},
        ).status_code
        == 413
    )


def test_zip_limit(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_EXPANDED", 100)
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 101)
    assert client.post("/audit", files={"file": ("large.docx", data.getvalue())}).status_code == 413


def test_cleanup(client, synthetic_dir, monkeypatch, tmp_path):
    from tempfile import TemporaryDirectory

    folders = []

    def temporary_directory(**kwargs):
        folder = TemporaryDirectory(dir=tmp_path, **kwargs)
        folders.append(folder.name)
        return folder

    monkeypatch.setattr(api, "TemporaryDirectory", temporary_directory)
    from pathlib import Path

    for data in [(synthetic_dir / "shelves.docx").read_bytes(), b"bad"]:
        client.post("/audit", files={"file": ("test.docx", data)})
    assert len(folders) == 2
    assert all(not Path(folder).exists() for folder in folders)
