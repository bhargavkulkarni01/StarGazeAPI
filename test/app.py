"""Dear ImGui test client for the Star Story API.

Run the backend first, then: python test/app.py
"""
import json
import threading
import time
from pathlib import Path

import requests
from imgui_bundle import imgui, immapp

IMAGES = sorted((Path(__file__).parent / "images").glob("*.jpg"))
POLL_SECONDS = 2

api_url = "http://localhost:8000"
fov = 0.0  # 0 = don't send a field-of-view hint
jobs: list[dict] = []  # {"name", "status", "stage", "json"}; worker threads update these
selected = 0
health = ""


def pretty(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def run_upload(job: dict, path: Path, url: str, fov_hint: float):
    try:
        data = {"fov": fov_hint} if fov_hint > 0 else {}
        with path.open("rb") as f:
            r = requests.post(f"{url}/analyze", files={"image": (path.name, f, "image/jpeg")}, data=data, timeout=60)
        job["json"] = pretty(r.json())
        if r.status_code != 202:
            job["status"] = f"HTTP {r.status_code}"
            return
        job_id = r.json()["job_id"]
        while True:
            body = requests.get(f"{url}/analyze/{job_id}", timeout=30).json()
            job.update(status=body.get("status", "?"), stage=body.get("stage", ""), json=pretty(body))
            if body.get("status") in ("success", "failed"):
                return
            time.sleep(POLL_SECONDS)
    except (requests.RequestException, ValueError) as e:  # ValueError: response wasn't JSON
        job.update(status="error", json=str(e))


def check_health():
    global health
    try:
        health = pretty(requests.get(f"{api_url}/health", timeout=5).json())
    except (requests.RequestException, ValueError) as e:
        health = str(e)


def gui():
    global api_url, fov, selected
    _, api_url = imgui.input_text("API URL", api_url)
    _, fov = imgui.input_float("FOV hint (deg, 0 = none)", fov, 5.0, 10.0, "%.1f")
    if imgui.button("Health check"):
        threading.Thread(target=check_health, daemon=True).start()
    if health:
        imgui.same_line()
        imgui.text(pretty(health))  
    
    imgui.separator_text("Upload an example image")
    if not IMAGES:
        imgui.text("No .jpg files in test/images, insert test jpegs there")
    for i, path in enumerate(IMAGES):
        if i:
            imgui.same_line()
        if imgui.button(path.stem):
            job = {"name": path.name, "status": "uploading", "stage": "", "json": ""}
            jobs.append(job)
            selected = len(jobs) - 1
            threading.Thread(target=run_upload, args=(job, path, api_url.rstrip("/"), fov), daemon=True).start()

    imgui.separator_text("Jobs")
    imgui.begin_child("jobs", imgui.ImVec2(300, 0), imgui.ChildFlags_.borders)
    for i, job in enumerate(jobs):
        label = f"{job['name']}  [{job['status']}] {job['stage']}##{i}"
        if imgui.selectable(label, i == selected)[0]:
            selected = i
    imgui.end_child()
    imgui.same_line()
    imgui.begin_child("response", imgui.ImVec2(0, 0), imgui.ChildFlags_.borders)
    if jobs:
        job = jobs[selected]
        story = json.loads(job["json"]).get("result", {}).get("story") if job["status"] == "success" else None
        if story:
            imgui.text_wrapped(story)
            imgui.separator()
        imgui.input_text_multiline("##json", job["json"], imgui.ImVec2(-1, -1), imgui.InputTextFlags_.read_only)
    else:
        imgui.text("Click an image button to start a job.")
    imgui.end_child()


if __name__ == "__main__":
    immapp.run(gui, window_title="Star Story API tester", window_size=(1100, 700))
