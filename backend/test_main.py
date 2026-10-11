import io
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASTROMETRY_API_KEY", "test")

from fastapi.testclient import TestClient
from PIL import Image

import main

client = TestClient(main.app)


def jpeg(color="black") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "JPEG")
    return buf.getvalue()


def test_duplicate_upload_shares_job(monkeypatch):
    monkeypatch.setattr(main, "run_job", lambda *a: None)  # leave the job "processing"
    a = client.post("/analyze", files={"image": ("a.jpg", jpeg("white"))}).json()["job_id"]
    b = client.post("/analyze", files={"image": ("a.jpg", jpeg("white"))}).json()["job_id"]
    assert a == b
    body = client.get(f"/analyze/{a}").json()
    assert body["status"] == "processing" and "cache_key" not in body


def test_rejects_bad_fov_and_huge_upload(monkeypatch):
    assert client.post("/analyze", files={"image": ("a.jpg", jpeg())}, data={"fov": "-5"}).status_code == 422
    assert client.post("/analyze", files={"image": ("a.jpg", jpeg())}, data={"fov": "200"}).status_code == 422
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    assert client.post("/analyze", files={"image": ("a.jpg", jpeg())}).status_code == 413


def test_unknown_job_404():
    assert client.get("/analyze/nope").status_code == 404


def test_run_job_builds_result(monkeypatch):
    replies = {
        "jobs/7/calibration": {"ra": 83.8, "dec": -5.4, "pixscale": 36.0, "orientation": 10},
        "jobs/7/annotations": {"annotations": [{"names": ["HD 1", "Rigel"], "type": "star", "pixelx": 1, "pixely": 2}]},
        "jobs/7/objects_in_field": {"objects_in_field": ["Rigel"]},
    }
    monkeypatch.setattr(main, "nova_login", lambda: "s")
    monkeypatch.setattr(main, "nova_upload", lambda *a: 1)
    monkeypatch.setattr(main, "wait_for_job", lambda *a: 7)
    monkeypatch.setattr(main, "nova_get", replies.__getitem__)
    monkeypatch.setattr(main.ai, "get_story", lambda c, s: f"{c}: {s}")
    main.JOBS["j"] = {"status": "processing", "created": 0}
    main.run_job("j", b"", (100, 50), None, "k")
    r = main.JOBS["j"]["result"]
    assert main.JOBS["j"]["status"] == "success"
    assert r["constellation"] == "Orion" and r["location"]["fov"] == 1.0
    assert r["stars"][0]["name"] == "Rigel" and r["story"] == "Orion: ['Rigel']"
    assert main.pc.get("k") == r
