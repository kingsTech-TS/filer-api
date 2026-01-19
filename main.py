import os
import hmac
import hashlib
import httpx  # Async HTTP client
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from dotenv import load_dotenv
from services.cloudconvert_service import CloudConvertService

load_dotenv()

API_KEY = os.getenv("CLOUDCONVERT_API_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", 20))

app = FastAPI(title="Real-Time File Converter API")

# CORS (Note: Browsers usually send Origin without trailing slash)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", 
        "https://filer-flame.vercel.app" 
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

service = CloudConvertService(API_KEY)

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

jobs = {}  # In-memory store for demo (Use Redis/DB in production)
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "png", "jpg", "jpeg", "mp4", "mp3"}

# ---------------- WEBHOOK VERIFICATION ----------------
def verify_signature(payload: bytes, signature_header: str):
    """
    Verifies that the webhook request came from CloudConvert.
    """
    if not signature_header:
        raise HTTPException(status_code=403, detail="No signature header")

    # CloudConvert format: t=...,v1=...
    # We need to extract the v1 signature and optionally check timestamp
    try:
        parts = {k: v for k, v in [x.split("=") for x in signature_header.split(",")]}
        signature = parts.get("v1")
        # timestamp = parts.get("t") # We can check freshness if needed
        
        if not signature:
             raise HTTPException(status_code=403, detail="Invalid signature header format")

        # Create the expected hash
        mac = hmac.new(
            WEBHOOK_SECRET.encode('utf-8'), 
            msg=payload, 
            digestmod=hashlib.sha256
        )
        expected_signature = mac.hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            raise HTTPException(status_code=403, detail="Invalid signature")
            
    except Exception as e:
        # Catch parsing errors or any other issues
        raise HTTPException(status_code=403, detail=f"Signature verification failed: {str(e)}")

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

    # Save uploaded file
    input_path = UPLOAD_DIR / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    # Start conversion job
    # Use environment variable for webhook URL if available
    default_webhook = "https://filer-api.onrender.com/webhook/cloudconvert"
    webhook_url = os.getenv("WEBHOOK_URL", default_webhook)
    
    try:
        job_id = await service.create_job(input_path, output_format, webhook_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create job: {str(e)}")

    # Save job in-memory
    jobs[job_id] = {
        "status": "processing",
        "progress": 0,
        "filename": file.filename
    }

    return {"job_id": job_id, "status": "processing"}

# ---------------- PROGRESS ----------------
@app.get("/progress/{job_id}")
def get_progress(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    
    return {"status": job["status"], "progress": job.get("progress", 0)}

# ---------------- WEBHOOK (UPDATED) ----------------
@app.post("/webhook/cloudconvert")
async def cloudconvert_webhook(request: Request):
    # 1. Read raw body for verification
    payload_bytes = await request.body()
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 2. Verify Security
    signature_header = request.headers.get("CloudConvert-Signature")
    verify_signature(payload_bytes, signature_header)

    job_id = payload.get("job", {}).get("id")
    if not job_id:
         # Some events might not be job related or structure differs, return ok to ignore
         return {"ok": True}

    if job_id not in jobs:
        # Log this incident, as it might be an attack or an orphan job
        print(f"Received webhook for unknown job: {job_id}")
        return JSONResponse(status_code=200, content={"ok": True}) # Return 200 to stop retries

    event = payload.get("event")
    
    if event == "job.finished":
        try:
            url, filename = service.extract_download_url(payload["job"])
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status() # Raise error if download failed

            output_path = OUTPUT_DIR / filename
            with open(output_path, "wb") as f:
                f.write(response.content)

            jobs[job_id]["status"] = "finished"
            jobs[job_id]["progress"] = 100
            jobs[job_id]["output"] = str(output_path)
            
        except Exception as e:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["message"] = f"Download failed: {str(e)}"

    elif event == "job.failed":
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["message"] = payload.get("job", {}).get("message", "Unknown error")

    else:
        # For progress updates or other events
        # Note: 'job.created' or 'task.finished' might also come here
        # CloudConvert sends nested task events sometimes if configured.
        # But top level job events have 'job' key.
        # We assume top level events.
        pass

    return {"ok": True}

# ---------------- DOWNLOAD ----------------
@app.get("/download/{job_id}")
def download(job_id: str):
    job = jobs.get(job_id)
    if not job or job.get("status") != "finished":
        raise HTTPException(status_code=404, detail="File not ready")

    output_path = Path(job.get("output", ""))
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Optional: Delete file after serving? 
    # return FileResponse(..., background=BackgroundTask(unlink, output_path))
    
    return FileResponse(output_path, filename=output_path.name)