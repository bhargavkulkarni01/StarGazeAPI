import io
import json
import os
import re
import threading
import time
import uuid

import anthropic
import requests
from astropy.coordinates import SkyCoord, get_constellation
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageOps

from services.ai_services import get_story

load_dotenv()

ASTROMETRY_URL = os.getenv("ASTROMETRY_API_URL", "https://nova.astrometry.net/api").rstrip("/")
ASTROMETRY_KEY = os.getenv("ASTROMETRY_API_KEY")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "3"))
SOLVE_TIMEOUT = float(os.getenv("SOLVE_TIMEOUT_SECONDS", "300"))
MAX_DIM = 2000          # downscale big photos before uploading
MAX_MARKERS = 60        # max annotated objects returned
JOB_TTL = 3600          # keep finished jobs in memory for 1 hour

app = FastAPI(title="Star Story API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# In-memory job store: job_id -> {"status", "stage", "result", "error", "created"}
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


class SolveError(Exception):
    pass

# ---------------------------------------------------------------- Astrometry.net
def nova_login() -> str:
    r = requests.post(
        f"{ASTROMETRY_URL}/login",
        data={"request-json": json.dumps({"apikey": ASTROMETRY_KEY})},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise SolveError(f"Astrometry.net login failed: {data.get('errormessage', data)}")
    return data["session"]


def nova_upload(session: str, jpeg: bytes, fov: float | None) -> int:
    req = {
        "session": session,
        "publicly_visible": "n",
        "allow_commercial_use": "n",
        "allow_modifications": "n",
    }
    if fov:  # scale hint makes solving much faster: estimated width in degrees, +/-20%
        req.update(scale_units="degwidth", scale_type="ev", scale_est=fov, scale_err=20)
    r = requests.post(
        f"{ASTROMETRY_URL}/upload",
        data={"request-json": json.dumps(req)},
        files={"file": ("sky.jpg", jpeg, "image/jpeg")},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise SolveError(f"Upload failed: {data.get('errormessage', data)}")
    return data["subid"]


def nova_get(path: str) -> dict:
    r = requests.get(f"{ASTROMETRY_URL}/{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def wait_for_job(subid: int, set_stage) -> int:
    """Poll the submission until it has a job, then poll the job until it finishes."""
    deadline = time.time() + SOLVE_TIMEOUT
    job_id = None
    while time.time() < deadline:
        if job_id is None:
            sub = nova_get(f"submissions/{subid}")
            jobs = [j for j in sub.get("jobs", []) if j]
            if jobs:
                job_id = jobs[0]
                set_stage("Solving star field…")
            else:
                set_stage("Waiting in Astrometry.net queue…")
        else:
            status = nova_get(f"jobs/{job_id}").get("status")
            if status == "success":
                return job_id
            if status == "failure":
                raise SolveError(
                    "Astrometry.net couldn't match this image. Try a sharper, dark-sky photo "
                    "with more visible stars, or give a field-of-view estimate."
                )
        time.sleep(POLL_INTERVAL)
    raise SolveError("Timed out waiting for Astrometry.net. Try again or add a field-of-view estimate.")


CATALOG_ID = re.compile(r"^(HD|HIP|HR|SAO|TYC|BD|GC|PGC|UGC|2MASS|Gaia|GSC|IRAS|PSR|WDS)\b", re.I)


def display_name(names: list[str]) -> tuple[str, bool]:
    """Prefer a non-catalog-number name (e.g. 'Rigel' over 'HD 34085')."""
    for n in names:
        if not CATALOG_ID.match(n):
            return n, True
    return (names[0] if names else "Unknown"), False


# ---------------------------------------------------------------- Job runner

def update(job_id: str, **fields):
    with LOCK:
        JOBS[job_id].update(fields)


def run_job(job_id: str, jpeg: bytes, size: tuple[int, int], fov: float | None):
    try:
        set_stage = lambda s: update(job_id, stage=s)

        set_stage("Logging in to Astrometry.net…")
        session = nova_login()
        set_stage("Uploading image…")
        subid = nova_upload(session, jpeg, fov)
        job = wait_for_job(subid, set_stage)

        set_stage("Reading results…")
        cal = nova_get(f"jobs/{job}/calibration")
        try:
            annotations = nova_get(f"jobs/{job}/annotations").get("annotations", [])
        except Exception:
            annotations = []
        try:
            objects = nova_get(f"jobs/{job}/objects_in_field").get("objects_in_field", [])
        except Exception:
            objects = []

        ra, dec = float(cal["ra"]), float(cal["dec"])
        constellation = get_constellation(SkyCoord(ra=ra, dec=dec, unit="deg"))
        width, height = size
        fov_deg = float(cal["pixscale"]) * width / 3600.0  # pixscale is arcsec/pixel

        stars = []
        for a in annotations:
            if "pixelx" not in a or "pixely" not in a:
                continue
            name, proper = display_name(a.get("names", []))
            stars.append({
                "name": name, "names": a.get("names", []), "type": a.get("type"),
                "proper": proper, "x": float(a["pixelx"]), "y": float(a["pixely"]),
            })
        stars = sorted(stars, key=lambda s: not s["proper"])[:MAX_MARKERS]

        set_stage("Writing the myth…")
        named = [s["name"] for s in stars if s["proper"]][:10]
        try:
            story = get_story(constellation, named)
        except Exception as e:
            story = f"(Could not generate story: {e})"

        update(job_id, status="success", stage="Done", result={
            "location": {"ra": ra, "dec": dec, "roll": float(cal["orientation"]), "fov": fov_deg},
            "constellation": constellation,
            "image_size": {"width": width, "height": height},
            "stars": stars,
            "objects_in_field": objects,
            "story": story,
            "astrometry_job_id": job,
        })
    except SolveError as e:
        update(job_id, status="failed", error=str(e))
    except requests.RequestException as e:
        update(job_id, status="failed", error=f"Astrometry.net request failed: {e}")
    except Exception as e:  # keep the job record useful instead of crashing the worker
        update(job_id, status="failed", error=f"Unexpected error: {e}")


# ---------------------------------------------------------------- Routes
@app.post("/analyze", status_code=202)
def analyze(
    background: BackgroundTasks,
    image: UploadFile = File(...),
    fov: float | None = Form(None),  # optional estimated width of the photo in degrees
):
    if not ASTROMETRY_KEY:
        raise HTTPException(503, "ASTROMETRY_API_KEY is not set on the server.")
    try:
        img = Image.open(io.BytesIO(image.file.read()))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_DIM, MAX_DIM))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
    except Exception:
        raise HTTPException(400, "Could not read the uploaded image.")

    job_id = uuid.uuid4().hex
    now = time.time()
    with LOCK:
        for k in [k for k, v in JOBS.items() if now - v["created"] > JOB_TTL]:
            del JOBS[k]
        JOBS[job_id] = {"status": "processing", "stage": "Queued…", "created": now}

    background.add_task(run_job, job_id, buf.getvalue(), img.size, fov)
    return {"job_id": job_id}


@app.get("/analyze/{job_id}")
def analyze_status(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        print("looking for job: {}", job_id)
        print("response: {}", job)
        if not job:
            raise HTTPException(404, "Unknown or expired job_id.")
        return {k: v for k, v in job.items() if k != "created"}