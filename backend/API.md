# Star Story API

A FastAPI server that turns a photo of the night sky into a list of named stars, the
constellation the photo is centered on, and a short myth about that constellation.

## How a request flows

```
POST /analyze ──► validate + hash photo ──► cache hit? ──yes──► job is "success" at once
                                               │ no
                                               ▼
                         same photo already processing? ──yes──► return that job_id
                                               │ no
                                               ▼
                 background job (run_job, main.py):
                   1. log in to Astrometry.net             (nova_login)
                   2. upload the downscaled JPEG           (nova_upload)
                   3. poll submission → job until solved   (wait_for_job)
                   4. fetch calibration, annotations and
                      objects_in_field in parallel         (nova_get / nova_list)
                   5. constellation from the center RA/Dec (astropy get_constellation)
                   6. story from Claude                    (services/ai_services.py)
                   7. store the result in the job; cache it if the story worked

GET /analyze/{job_id} ──► current status, stage text, and result or error
```

Plate solving often takes 30 s or more, so the client starts a job and then polls it.

## Endpoints

### `POST /analyze`

Starts an analysis. `multipart/form-data`:

| Field | Required | Meaning |
|---|---|---|
| `image` | yes | The photo (any format Pillow reads), at most 25 MB |
| `fov` | no | Estimated photo width in degrees, `0 < fov ≤ 180`. Speeds up solving a lot. The solver searches ½× to 2× this value, so a rough guess is fine; a guess outside that range makes the solve fail. |

Returns `202 {"job_id": "..."}`.

| Status | When |
|---|---|
| 400 | The file isn't a readable image |
| 413 | The file is larger than 25 MB |
| 422 | `image` missing, or `fov` out of range |
| 503 | `ASTROMETRY_API_KEY` isn't set on the server |

```bash
curl -X POST http://localhost:8000/analyze -F "image=@sky.jpg" -F "fov=60"
```

Uploading the same bytes again:
- while the first job is still running returns the **same** `job_id` (no second solve);
- after it succeeded (within 24 h) returns a new job that is already `success`.

### `GET /analyze/{job_id}`

Poll every 2–3 seconds until `status` is `success` or `failed`. Unknown or expired ids
return 404. Jobs are kept for 1 hour.

```json
{"status": "processing", "stage": "Solving star field…"}
```

Stages, in order: `Queued…`, `Logging in to Astrometry.net…`, `Uploading image…`,
`Waiting in Astrometry.net queue…`, `Solving star field…`, `Reading results…`,
`Writing the myth…`, `Done`.

Success:

```json
{
  "status": "success",
  "stage": "Done",
  "result": {
    "location": {"ra": 83.8, "dec": -5.4, "roll": 10.0, "fov": 21.3},
    "constellation": "Orion",
    "image_size": {"width": 2000, "height": 1500},
    "stars": [
      {"name": "Rigel", "names": ["Rigel", "HD 34085"], "type": "bright",
       "proper": true, "x": 812.4, "y": 1021.7}
    ],
    "objects_in_field": ["The star Rigel (βOri)", "..."],
    "story": "The Hunter's Fall\n...",
    "astrometry_job_id": 1234567
  }
}
```

Failure:

```json
{"status": "failed", "stage": "Solving star field…", "error": "Astrometry.net couldn't match this image. ..."}
```

### `GET /health`

```json
{"ok": true, "astrometry_key_set": true}
```

## Result fields

| Field | Meaning |
|---|---|
| `location.ra`, `location.dec` | Center of the photo, J2000, degrees |
| `location.roll` | Rotation of the photo, degrees East of North (Astrometry.net `orientation`) |
| `location.fov` | Solved width of the photo, degrees |
| `constellation` | IAU constellation containing the center point |
| `image_size` | Size of the **downscaled** image (longest side ≤ 2000 px) that star `x`/`y` refer to. Scale by `original_width / image_size.width` to draw on the original. |
| `stars` | Up to 60 annotated objects, proper names first. `name` is the first non-catalog name; `proper` is false when only catalog ids like `HD 34085` exist. |
| `objects_in_field` | Astrometry.net's plain-text list of objects in the frame |
| `story` | Title line plus ~100 words. If Claude fails, a fallback line; such results are not cached, so re-uploading retries the story. |
| `astrometry_job_id` | Look the solve up at `https://nova.astrometry.net/status/<id>` |

## Configuration (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | The server won't start without it |
| `ASTROMETRY_API_KEY` | required | `/analyze` returns 503 without it |
| `ANTHROPIC_WORKSPACE_ID` | unset | Only for organization-scoped keys |
| `ASTROMETRY_API_URL` | `https://nova.astrometry.net/api` | Point at a self-hosted solver |
| `POLL_INTERVAL_SECONDS` | `3` | How often to poll Astrometry.net |
| `SOLVE_TIMEOUT_SECONDS` | `300` | Give up after this long |

Constants in `main.py`: `MAX_DIM` (2000 px downscale), `MAX_UPLOAD_BYTES` (25 MB),
`MAX_MARKERS` (60), `JOB_TTL` (1 h). Cache size and TTL are `PhotoCache` arguments
(500 items, 24 h).

## Reliability and performance

- One shared `requests.Session` reuses connections to Astrometry.net and retries GETs up to
  3 times with exponential backoff on connection errors and 500/502/503/504. Login and upload
  POSTs are not retried, so a photo is never submitted twice.
- Annotations and objects_in_field are optional: if either fails, the job still succeeds
  with an empty list. Calibration is required.
- Photos are rotated by their EXIF orientation and downscaled before upload.
- Results are cached by the SHA-256 of the uploaded bytes.

## Limits to know

- All state (jobs, cache) is in memory, in one process. A restart loses it, and running
  several uvicorn workers would split it (a poll can hit a worker that doesn't know the job).
  Run a single worker.
- There is no auth or rate limiting. Anyone who can reach the server can spend your
  Astrometry.net and Anthropic quota. Put it behind a proxy with auth before exposing it.
- Each running job holds one thread from FastAPI's threadpool (40 by default) for up to
  `SOLVE_TIMEOUT_SECONDS`.
- Photos go to nova.astrometry.net (marked private). Don't send photos you can't share.

## Where and how AI is used

### At runtime: the story (one place only)

Claude writes the myth in step 6 of the job. Everything else (plate solving, star names,
constellation) is plain code and Astrometry.net, not AI.

| What | Where |
|---|---|
| Call site | `run_job` in `main.py` calls `ai.get_story(constellation, named)` |
| Client | `AnthropicAI` in `services/ai_services.py` (official `anthropic` SDK) |
| Model | `claude-haiku-5-5` (`MODEL`), effort `low`, `max_tokens=2000`, 30 s timeout |
| Prompt | `SYSTEM_PROMPT`: tell the constellation's myth, title line + ~100 words, name the origin culture, mention only stars that matter to the story |
| Input sent | Only text: the constellation name and up to 10 proper star names, e.g. `Constellation: Orion. Objects identified in the photo: Rigel, Betelgeuse.` The photo is **never** sent to Anthropic. |
| Output | The text blocks of the reply joined into `result.story` |

If the call fails (API error, empty reply, or a crash), the job still succeeds with the
fallback line `STORY_FALLBACK`, and that result isn't cached so a re-upload retries the
story. A reply cut short (`stop_reason` other than `end_turn`) is logged with its request id.

To change the story's tone or length, edit `SYSTEM_PROMPT`; to change the model, edit `MODEL`.
The story is generated text: the myths may be simplified or wrong, so don't present them as
sourced fact.

### During development

Some of the backend API, the story prompt and these docs were written with help from Claude
(Anthropic's AI). See the "AI Warning" in the top-level README.\

`test_main.py` mocks both Astrometry.net and Claude, so the tests check our code, not the AI.
If something is wrong, please open an issue.

## Tests

```bash
cd backend
pip install -r requirements.txt pytest httpx
pytest -q
```

`test_main.py` mocks Astrometry.net and Claude, so it needs no network or real keys.
