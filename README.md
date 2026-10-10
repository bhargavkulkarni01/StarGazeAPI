# Star Story

Photo of the night sky → Astrometry.net plate solve → constellation → mythological story from Claude Haiku 5.5 (`claude-haiku-5-5`).

- `backend/` FastAPI server (Astrometry.net API + astropy + Anthropic SDK)
- `App.source.tsx` the app code (TypeScript), copied over `mobile/App.tsx` after you create the React Native project
- `mobile/` React Native project (iOS + Android, no Expo), created in step 2

## Prerequisites

| For | You need |
|---|---|
| Everything | Node 18+, Python 3.10+, an Anthropic API key, an Astrometry.net API key |
| iOS | macOS, Xcode (with a simulator runtime installed), CocoaPods |
| Android | Android Studio, an emulator (AVD), JDK 17, `ANDROID_HOME` environment variable set |

Get an Astrometry.net key: sign in at https://nova.astrometry.net, open your profile/API page, and copy the API key.

## 1. Backend

```bash
cd star-app/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set ANTHROPIC_API_KEY and ASTROMETRY_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8000
```

Check it's running: `http://localhost:8000/health` should return
`{"ok": true, "astrometry_key_set": true}`. Leave this terminal running.

## 2. Create the React Native project in `~/workspace/bk_workspace/StarGazeApp/`

The `~/workspace/bk_workspace/StarGazeApp/` folder is created by the React Native CLI, so it must not exist yet.
Run this from the `star-app/` folder (next to `backend/` and `App.source.tsx`):

```bash
cd star-app
npx @react-native-community/cli@latest init StarStory --directory mobile
```

The project name is `StarStory` (used for the iOS/Android app names); the files go straight
into `mobile/` with no extra subfolder.

## 3. Add the app code

The React Native template already created `mobile/App.tsx`. Overwrite it with ours:

```bash
cp App.source.tsx mobile/App.tsx
cd mobile
```

(On Windows, copy `App.source.tsx` to `mobile\App.tsx`, replacing the existing file.)

## 4. Install the image picker

From inside `mobile/`:

```bash
npm install react-native-image-picker
```

## 5. Add permissions

**Android:** open `mobile/android/app/src/main/AndroidManifest.xml` and add this line right
after the opening `<manifest ...>` tag:

```xml
<uses-permission android:name="android.permission.CAMERA" />
```

**iOS (macOS only):** open `mobile/ios/StarStory/Info.plist` and add these inside the
top-level `<dict>`:

```xml
<key>NSCameraUsageDescription</key>
<string>Take a photo of the night sky to identify stars.</string>
<key>NSPhotoLibraryUsageDescription</key>
<string>Pick a night sky photo to identify stars.</string>
```

Then install the iOS pods:

```bash
cd ios
bundle install
bundle exec pod install        # if bundle fails, use: pod install
cd ..
```

## 6. Run

Make sure the backend from step 1 is still running. Run these from `mobile/`.

**iOS**
```bash
yarn ios
```
Or open `mobile/ios/StarStory.xcworkspace` in Xcode (not `.xcodeproj`), pick a simulator, and
press Run. If you use Xcode, run `npx react-native start` in a terminal first for Metro.

**Android**
1. Open Android Studio → Device Manager and start an emulator.
2. Run:
```bash
npx react-native run-android
```

## 7. Test it

- Drag a night-sky photo onto the simulator/emulator window to save it to the gallery.
- Tap **Gallery**, pick the photo, optionally enter a field of view in degrees, and tap
  **Identify stars**.
- The iOS simulator has no camera. The Android emulator's camera shows a fake scene, so use
  Gallery on both.

## Backend URL

`App.tsx` picks the URL per platform:

- iOS simulator: `http://localhost:8000`
- Android emulator: `http://10.0.2.2:8000` (the emulator's alias for your computer)
- Physical device: edit `API_URL` in `App.tsx` to your computer's LAN IP, e.g.
  `http://192.168.1.20:8000`. iOS also needs an App Transport Security exception for plain
  HTTP, and Android release builds block cleartext HTTP, so use HTTPS there.

## API (asynchronous)

Solving takes a while (often 30s+), so it is a start-then-poll flow.

**Start a job**
```bash
curl -X POST http://localhost:8000/analyze \
  -F "image=@/full/path/to/night-sky.jpg" \
  -F "fov=60"
# {"job_id":"3f2c..."}
```
`fov` is optional: the estimated width of the photo in degrees. It makes solving much faster.

**Poll the job** until `status` is `success` or `failed`
```bash
curl -s http://localhost:8000/analyze/<job_id> | jq
```
```json
{"status": "processing", "stage": "Solving star field…"}
```
On success:
```json
{
  "status": "success",
  "stage": "Done",
  "result": {
    "location": {"ra": 0, "dec": 0, "roll": 0, "fov": 0},
    "constellation": "Orion",
    "image_size": {"width": 0, "height": 0},
    "stars": [{"name": "Rigel", "names": ["Rigel"], "type": "star", "proper": true, "x": 0, "y": 0}],
    "objects_in_field": ["..."],
    "story": "..."
  }
}
```
On failure: `{"status": "failed", "error": "..."}`. `GET /health` returns server status.

Optional env vars: `ASTROMETRY_API_URL`, `POLL_INTERVAL_SECONDS` (default 3),
`SOLVE_TIMEOUT_SECONDS` (default 300).

## Notes

- Astrometry.net solves blind (no prior position) at almost any scale, so normal phone photos
  work, unlike tetra3's narrow default database. A field-of-view estimate speeds it up a lot.
- The photo is uploaded to nova.astrometry.net (as private, non-public). Don't use it for
  images you can't share with a third party. The public service is shared, so solves can queue.
- The constellation comes from the solved center coordinates (astropy `get_constellation`).
  Star and object names come from Astrometry.net's annotations; entries that are only catalog
  numbers (e.g. `HD 34085`) are returned but not drawn as markers.
- Jobs are kept in server memory for an hour, so restarting the backend loses them.
- The Anthropic and Astrometry.net keys stay on the server; the app never sees them.

## Troubleshooting

- **Network request failed:** the backend isn't running, or it wasn't started with
  `--host 0.0.0.0`.
- **`ASTROMETRY_API_KEY is not set` (503):** add it to `backend/.env` and restart uvicorn.
- **Job fails with "couldn't match":** use a sharper photo with more visible stars, avoid heavy
  light pollution, and set a field-of-view estimate.
- **Timed out:** raise `SOLVE_TIMEOUT_SECONDS`, or provide `fov`. Blind solves can be slow.
- **Astrometry.net login failed:** double-check the API key.
- **Android build errors about SDK or Java:** check that `ANDROID_HOME` points to your SDK
  folder and `java -version` shows 17.

  # AI Warning
  The back end API, story-telling, and docs were facilitated with the use of Claude, Anthropic's AI agent. Like many other AIs, Claude can be wrong. If there is an issue, please do not hesitate to open an issue.
