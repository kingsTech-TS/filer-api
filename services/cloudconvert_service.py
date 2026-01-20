import httpx
from pathlib import Path
import json

class CloudConvertService:
    BASE_URL = "https://api.cloudconvert.com/v2"

    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def create_job(self, input_path: Path, output_format: str, webhook_url: str):
        async with httpx.AsyncClient() as client:
            job_res = await client.post(
                f"{self.BASE_URL}/jobs",
                headers=self.headers,
                json={
                    "tasks": {
                        "upload": {
                            "operation": "import/upload"
                        },
                        "convert": {
                            "operation": "convert",
                            "input": "upload",
                            "input_format": input_path.suffix.lstrip(".").lower(),
                            "output_format": output_format
                        },
                        "export": {
                            "operation": "export/url",
                            "input": "convert"
                        }
                    },
                    "webhook": {
                        "url": webhook_url,
                        "events": ["job.finished", "job.failed"]
                    }
                }
            )
            job_res.raise_for_status()
            job = job_res.json()["data"]

            upload_task = next(
                t for t in job["tasks"] if t["operation"] == "import/upload"
            )

            # Upload file
            with open(input_path, "rb") as f:
                # Note: CloudConvert presigned URL upload usually works with standard POST/PUT.
                # httpx handles file uploads gracefully.
                files = {"file": f}
                await client.post(
                    upload_task["result"]["form"]["url"],
                    data=upload_task["result"]["form"]["parameters"],
                    files=files
                )

            return job["id"]

    def extract_download_url(self, job_data: dict):
        """
        Extracts the download URL and filename from a finished job.
        """
        # Find the export task
        export_task = next(
            (t for t in job_data.get("tasks", []) if t.get("operation") == "export/url" and t.get("status") == "finished"),
            None
        )
        
        if not export_task:
            # Fallback: maybe look for any finished task with 'result' containing 'files'
            # But based on create_job structure, 'export' is the one we want.
            raise ValueError("No finished export task found in job data")
            
        result_files = export_task.get("result", {}).get("files", [])
        if not result_files:
            raise ValueError("No files found in export task result")
            
        # Assuming single file output for now
        file_info = result_files[0]
        return file_info["url"], file_info["filename"]
