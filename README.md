# Rematch Performance Lab — MVP

An iPhone-friendly, hosted-first starter for analysing Rematch gameplay videos.

## Status
This is a deployable starter project, not a website already published to the internet. The backend upload/frame-extraction path has been smoke-tested locally with a generated MP4. A real Rematch recording and a live hosted deployment still need to be tested.

## Included
- Responsive mobile web interface.
- Backend video upload (not browser-only HTML video loading).
- FFmpeg/ffprobe validation and timestamped frame extraction.
- Manual event tagging with timestamp, category, judgement, notes and confidence.
- JSON/CSV export.
- Optional AI analysis through an OpenAI-compatible vision model configured server-side.
- Optional shared API access token for private single-user deployments.

## Limitations and safety
- Automatic Rematch-specific event recognition is not trained yet.
- AI reports may be wrong; verify claims against the recording.
- Manual labels are stored in the current browser's local storage. They do not yet sync between devices or persist in a server database.
- Render's free instance uses ephemeral disk storage. Uploaded video files and extracted frames can disappear after restarts or redeploys. This is suitable for initial testing only, not permanent match archives.
- Before sharing the app publicly or storing sensitive recordings, configure `API_ACCESS_TOKEN`, use durable object storage, add user-level authentication, rate limits, quotas and file-retention controls.
- No API key should ever be placed in frontend code. Configure `OPENAI_API_KEY` on the backend only.

## Deploy with Render
1. Create or connect a GitHub repository containing this project.
2. In Render, create a new **Blueprint** using the repository and the root `render.yaml`.
3. Render should create the static site and API service. The API service installs FFmpeg in its Docker image.
4. Set `API_ACCESS_TOKEN` to a long random secret in the API service environment. Keep it private.
5. If you want AI analysis, set `OPENAI_API_KEY` and `VISION_MODEL` on the API service. Use a vision-capable model available to your account.
6. Wait for both services to deploy. Open the static site's public URL, tap **Settings**, enter the API service URL and the same API access token, then save.
7. Tap **Check backend**, upload a short test MP4, and verify that frames appear. Then test with a short Rematch recording from your iPhone.

**Cost note:** the Blueprint uses free Render services for the first test. Free service storage is ephemeral. Durable hosting for long-term video storage may require paid infrastructure; do not upgrade or incur costs without checking the current plan/pricing first.

## Run locally
Requirements: Python 3.11+, FFmpeg and ffprobe installed and available on PATH.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
DATA_DIR=./data API_ACCESS_TOKEN=local-test-token uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open `frontend/index.html`, open Settings, set API URL to `http://localhost:8000` and enter `local-test-token`. If testing from an iPhone, `localhost` means the iPhone itself; the backend must be reachable over your network or hosted online.

## API endpoints
- `GET /health`
- `POST /api/uploads` — multipart upload, video metadata and extracted frames.
- `GET /api/videos/{video_id}/frames` — frame timestamps and relative URLs.
- `GET /media/{video_id}/{filename}` — extracted frame images.
- `POST /api/videos/{video_id}/analyse` — optional AI analysis of sampled frames.
- `GET /api/videos/{video_id}/analysis` — latest AI report if available.

## Next engineering steps
1. Deploy and verify with a real iPhone Rematch recording.
2. Add authentication beyond a shared token, durable object storage and a relational database.
3. Build a curated Rematch event taxonomy and labelled dataset.
4. Measure precision/recall against held-out clips before describing any detector as automatic.
5. Add model training only when enough high-quality labels exist.
