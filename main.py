import os
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pathlib import Path
from dotenv import load_dotenv
from services.cloudconvert_service import CloudConvertService

load_dotenv()

API_KEY = os.getenv("CLOUDCONVERT_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", 20))

app = FastAPI(title="Real-Time File Converter API")

service = CloudConvertService(API_KEY)

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

jobs = {}  # Replace with DB in production

ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "png", "jpg", "jpeg", "mp4", "mp3"}


# ---------------- FILE VALIDATION ----------------
def validate_file(file: UploadFile):
    ext = file.filename.split(".")[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    file.file.seek(0, 2)
    size_mb = file.file.tell() / (1024 * 1024)
    file.file.seek(0)

    if size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {MAX_FILE_SIZE_MB}MB)"
        )


# ---------------- CONVERT ----------------
@app.post("/convert")
async def convert_file(
    file: UploadFile = File(...),
    output_format: str = "pdf"
):
    validate_file(file)

    input_path = UPLOAD_DIR / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    webhook_url = "https://your-domain.com/webhook/cloudconvert"

    job_id = service.create_job(input_path, output_format, webhook_url)

    jobs[job_id] = {
        "status": "processing",
        "filename": file.filename
    }

    return {
        "job_id": job_id,
        "status": "processing"
    }


# ---------------- WEBHOOK (REAL-TIME) ----------------
@app.post("/webhook/cloudconvert")
async def cloudconvert_webhook(request: Request):
    payload = await request.json()

    if payload.get("event") == "job.finished":
        job_id = payload["job"]["id"]

        url, filename = service.extract_download_url(payload["job"])
        response = requests.get(url)

        output_path = OUTPUT_DIR / filename
        with open(output_path, "wb") as f:
            f.write(response.content)

        jobs[job_id]["status"] = "finished"
        jobs[job_id]["output"] = str(output_path)

    if payload.get("event") == "job.failed":
        job_id = payload["job"]["id"]
        jobs[job_id]["status"] = "failed"

    return {"ok": True}


# ---------------- DOWNLOAD ----------------
@app.get("/download/{job_id}")
def download(job_id: str):
    job = jobs.get(job_id)

    if not job or job["status"] != "finished":
        raise HTTPException(status_code=404, detail="File not ready")

    return {
        "file": job["output"]
    }
