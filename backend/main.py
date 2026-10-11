import io
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from photocache import PhotoCache
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from astropy.coordinates import SkyCoord, get_constellation
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageOps

from services.ai_services import AnthropicAI

load_dotenv()

log = logging.getLogger(__name__)

ASTROMETRY_URL = os.getenv("ASTROMETRY_API_URL", "https://nova.astrometry.net/api").rstrip("/")
ASTROMETRY_KEY = os.getenv("ASTROMETRY_API_KEY")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "3"))
SOLVE_TIMEOUT = float(os.getenv("SOLVE_TIMEOUT_SECONDS", "300"))
MAX_DIM = 2000          # downscale big photos before uploading
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # reject bigger uploads before decoding
MAX_MARKERS = 60        # max annotated objects returned
JOB_TTL = 3600          # keep finished jobs in memory for 1 hour

app = FastAPI(title="Star Story API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
ai = AnthropicAI()
pc = PhotoCache()
STORY_FALLBACK = "The stars are quiet tonight... try again in a moment."

# In-memory job store: job_id -> {"status", "stage", "result", "error", "created"}
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


class SolveError(Exception):
    pass


# Shared session: reuses connections and retries transient errors on GETs (default Retry
# methods exclude POST, so an upload is never submitted twice).
http = requests.Session()
http.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])))

# ---------------------------------------------------------------- Astrometry.net
def nova_login() -> str:
    r = http.post(
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
    if fov:  # scale hint speeds solving a lot; wide bounds because a human guess outside them fails the solve
        req.update(scale_units="degwidth", scale_type="ul", scale_lower=str(fov / 2), scale_upper=str(fov * 2))
    r = http.post(
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
    r = http.get(f"{ASTROMETRY_URL}/{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def nova_list(path: str, key: str) -> list:
    """Optional extras: an empty list instead of failing the whole job."""
    try:
        return nova_get(path).get(key, [])
    except Exception:
        log.warning("Astrometry.net %s failed", path, exc_info=True)
        return []


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


def run_job(job_id: str, jpeg: bytes, size: tuple[int, int], fov: float | None, cache_key: str):
    try:
        set_stage = lambda s: update(job_id, stage=s)

        set_stage("Logging in to Astrometry.net…")
        session = nova_login()
        set_stage("Uploading image…")
        subid = nova_upload(session, jpeg, fov)
        job = wait_for_job(subid, set_stage)

        set_stage("Reading results…")
        with ThreadPoolExecutor(3) as ex:  # three independent requests, fetch in parallel
            cal_f = ex.submit(nova_get, f"jobs/{job}/calibration")
            ann_f = ex.submit(nova_list, f"jobs/{job}/annotations", "annotations")
            obj_f = ex.submit(nova_list, f"jobs/{job}/objects_in_field", "objects_in_field")
            cal, annotations, objects = cal_f.result(), ann_f.result(), obj_f.result()

        ra, dec = float(cal["ra"]), float(cal["dec"])
        constellation = str(get_constellation(SkyCoord(ra=ra, dec=dec, unit="deg")))
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
            story = ai.get_story(constellation, named)
        except Exception:
            log.exception("Story generation crashed")
            story = None
        story_ok = bool(story)

        result = {
            "location": {"ra": ra, "dec": dec, "roll": float(cal["orientation"]), "fov": fov_deg},
            "constellation": constellation,
            "image_size": {"width": width, "height": height},
            "stars": stars,
            "objects_in_field": objects,
            "story": story or STORY_FALLBACK,
            "astrometry_job_id": job,
        }
        update(job_id, status="success", stage="Done", result=result)
        if story_ok:  # don't cache the fallback, so the next upload retries the story
            pc.set(cache_key, result)
    except SolveError as e:
        update(job_id, status="failed", error=str(e))
    except requests.RequestException as e:
        update(job_id, status="failed", error=f"Astrometry.net request failed: {e}")
    except Exception as e:  # keep the job record useful instead of crashing the worker
        log.exception("Job %s crashed", job_id)
        update(job_id, status="failed", error=f"Unexpected error: {e}")


# ---------------------------------------------------------------- Routes
@app.post("/analyze", status_code=202)
def analyze(
    background: BackgroundTasks,
    image: UploadFile = File(...),
    fov: float | None = Form(None, gt=0, le=180),  # optional estimated width of the photo in degrees
):
    if not ASTROMETRY_KEY:
        raise HTTPException(503, "ASTROMETRY_API_KEY is not set on the server.")
    raw = image.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    cache_key = PhotoCache.key(raw)
    cached = pc.get(cache_key)

    if cached is None:
        try:
            img = Image.open(io.BytesIO(raw))
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
        # same photo already being solved (e.g. a double tap): share that job instead of solving twice
        for k, v in JOBS.items():
            if v["status"] == "processing" and v.get("cache_key") == cache_key:
                return {"job_id": k}
        if cached is not None:  # same photo seen before: finish immediately
            JOBS[job_id] = {"status": "success", "stage": "Done", "result": cached, "created": now}
        else:
            JOBS[job_id] = {"status": "processing", "stage": "Queued…", "created": now, "cache_key": cache_key}

    if cached is None:
        background.add_task(run_job, job_id, buf.getvalue(), img.size, fov, cache_key)
    return {"job_id": job_id}


@app.get("/analyze/{job_id}")
def analyze_status(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown or expired job_id.")
        return {k: v for k, v in job.items() if k not in ("created", "cache_key")}


@app.get("/health")
def health_check():
    return {
        "ok": True,
        "astrometry_key_set": bool(ASTROMETRY_KEY),
    }
