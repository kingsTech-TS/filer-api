import requests
from pathlib import Path

class CloudConvertService:
    BASE_URL = "https://api.cloudconvert.com/v2"

    def __init__(self, api_key: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def create_job(self, input_path: Path, output_format: str, webhook_url: str):
        job_res = requests.post(
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

        with open(input_path, "rb") as f:
            requests.post(
                upload_task["result"]["form"]["url"],
                data=upload_task["result"]["form"]["parameters"],
                files={"file": f}
            )

        return job["id"]
