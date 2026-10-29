import os, uuid, json, shutil, subprocess, time, hmac
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
FRAME_DIR = DATA_DIR / "frames"
REPORT_DIR = DATA_DIR / "reports"
for p in (UPLOAD_DIR, FRAME_DIR, REPORT_DIR):
    p.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
ALLOWED_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
app = FastAPI(title="Rematch Performance Lab API", version="0.1.0")

origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def protect_api(request, call_next):
    # Optional shared access token for private single-user deployments.
    # Health remains public so hosting platforms can perform health checks.
    expected = os.getenv("API_ACCESS_TOKEN", "").strip()
    if expected and request.method != "OPTIONS" and (request.url.path.startswith("/api/") or request.url.path.startswith("/media/")):
        supplied = request.headers.get("x-api-token", "")
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid API access token. Set it in Settings."})
    return await call_next(request)

def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=True).stdout
    except subprocess.TimeoutExpired:
        raise HTTPException(422, "Video processing timed out. Try a shorter clip.")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "FFmpeg could not process this video.")[-1200:]
        raise HTTPException(422, detail)

def probe(path: Path):
    raw = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration,format_name,size", "-of", "json", str(path)])
    data = json.loads(raw).get("format", {})
    try: duration = float(data.get("duration", 0))
    except (ValueError, TypeError): duration = 0
    if duration <= 0:
        raise HTTPException(422, "Could not determine video duration. Try exporting as MP4.")
    return {"duration_seconds": duration, "format": data.get("format_name", "unknown"),
            "size_bytes": int(data.get("size", path.stat().st_size))}

@app.get("/health")
def health():
    return {"ok": True, "service": "rematch-performance-lab-api", "time": int(time.time())}

@app.post("/api/uploads")
async def upload_video(file: UploadFile = File(...)):
    name = Path(file.filename or "gameplay.mp4").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(415, "Unsupported video type. Use MP4, MOV, M4V, WebM or MKV.")
    video_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{video_id}{ext}"
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                total += len(chunk)
                if total > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"Video exceeds the {MAX_UPLOAD_MB} MB upload limit.")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    try:
        metadata = probe(dest)
        if metadata["duration_seconds"] > 60 * 60:
            raise HTTPException(413, "For this prototype, videos must be 60 minutes or shorter.")
        out_dir = FRAME_DIR / video_id
        out_dir.mkdir(parents=True, exist_ok=True)
        # One preview frame every 5 seconds, capped at 240 frames. Accurate timestamps in filenames.
        interval = max(1.0, metadata["duration_seconds"] / 240.0)
        # The fps filter can omit the final expected sample on short clips.
        # Select frames by presentation timestamp with a small tolerance instead.
        select_filter = f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval:.6f}),scale=960:-2:force_original_aspect_ratio=decrease"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(dest),
             "-vf", select_filter, "-vsync", "0", "-frames:v", "240", "-q:v", "3",
             str(out_dir / "frame_%06d.jpg")])
        frames = sorted(out_dir.glob("frame_*.jpg"))
        if not frames:
            raise HTTPException(422, "The video opened but no frames could be extracted.")
        frame_records = []
        for i, fp in enumerate(frames):
            frame_records.append({
                "index": i,
                "timestamp_seconds": round(i * interval, 2),
                "url": f"/media/{video_id}/{fp.name}",
                "filename": fp.name
            })
        meta = {"video_id": video_id, "original_name": name, **metadata,
                "sample_interval_seconds": round(interval, 2), "frames": frame_records,
                "uploaded_at": int(time.time())}
        (REPORT_DIR / f"{video_id}.json").write_text(json.dumps(meta, indent=2))
        return meta
    except Exception:
        # Keep upload only if metadata processing succeeded; clean malformed files on failure.
        if not (REPORT_DIR / f"{video_id}.json").exists():
            dest.unlink(missing_ok=True)
            shutil.rmtree(FRAME_DIR / video_id, ignore_errors=True)
        raise

@app.get("/api/videos/{video_id}/frames")
def get_frames(video_id: str):
    path = REPORT_DIR / f"{video_id}.json"
    if not path.exists(): raise HTTPException(404, "Video not found.")
    return json.loads(path.read_text())["frames"]

@app.get("/media/{video_id}/{filename}")
def get_frame(video_id: str, filename: str):
    # Prevent path traversal and only serve JPG frames for a known video.
    if Path(filename).name != filename or not filename.endswith(".jpg"):
        raise HTTPException(400, "Invalid frame path.")
    path = FRAME_DIR / video_id / filename
    if not path.exists(): raise HTTPException(404, "Frame not found.")
    return FileResponse(path, media_type="image/jpeg")

class AnalyseRequest(BaseModel):
    prompt: Optional[str] = None
    max_frames: int = 12

@app.post("/api/videos/{video_id}/analyse")
def analyse(video_id: str, req: AnalyseRequest):
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("VISION_MODEL")
    if not api_key or not model:
        raise HTTPException(503, "AI analysis is not configured. Set OPENAI_API_KEY and VISION_MODEL on the backend.")
    meta_path = REPORT_DIR / f"{video_id}.json"
    if not meta_path.exists(): raise HTTPException(404, "Video not found.")
    meta = json.loads(meta_path.read_text())
    frames = meta["frames"]
    cap = max(1, min(int(req.max_frames), 24))
    if len(frames) > cap:
        picks = [round(i * (len(frames)-1) / (cap-1)) for i in range(cap)] if cap > 1 else [0]
        frames = [frames[i] for i in picks]
    try:
        from openai import OpenAI
        import base64
        client = OpenAI(api_key=api_key)
        content = [{
            "type": "text",
            "text": (
                "You are a cautious gameplay video analyst. Analyse only what is visible in these "
                "timestamped frames. They are samples from one match, not necessarily consecutive. "
                "Do not invent game mechanics, player positions, intent, or outcomes. Distinguish "
                "direct observation from tactical interpretation. For every claim include the frame "
                "timestamp(s). Mark uncertainty explicitly. Return a concise report with sections: "
                "visible observations, candidate decision points, possible strengths, possible risks, "
                "unknowns, and next clips to inspect. Do not assert an error unless evidence supports it. "
                f"User's focus: {req.prompt or 'general decision-making and positioning'}. "
                "Video metadata: " + json.dumps({k: meta[k] for k in ("original_name","duration_seconds","format")})
            )
        }]
        for frame in frames:
            p = FRAME_DIR / video_id / frame["filename"]
            encoded = base64.b64encode(p.read_bytes()).decode("ascii")
            content.append({"type":"text","text":f"Frame timestamp: {frame['timestamp_seconds']} seconds"})
            content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{encoded}","detail":"low"}})
        response = client.chat.completions.create(
            model=model,
            messages=[{"role":"user","content":content}],
            temperature=0.2
        )
        report = {
            "video_id": video_id, "created_at": int(time.time()), "model": model,
            "frames_analysed": [{"timestamp_seconds":f["timestamp_seconds"],"filename":f["filename"]} for f in frames],
            "report": response.choices[0].message.content or "",
            "warning": "AI interpretations are provisional. Verify each claim against the recording."
        }
        (REPORT_DIR / f"{video_id}-analysis.json").write_text(json.dumps(report, indent=2))
        return report
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"AI analysis failed: {str(e)[:500]}")

@app.get("/api/videos/{video_id}/analysis")
def get_analysis(video_id: str):
    p = REPORT_DIR / f"{video_id}-analysis.json"
    if not p.exists(): raise HTTPException(404, "No analysis report exists yet.")
    return json.loads(p.read_text())
