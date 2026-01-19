import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
import httpx

# Mocking the imports before importing main to avoid side effects (like loading .env or connecting to things)
# However, we are testing unit logic, so we can import.
# Since we need to test verify_signature which is in main, we need to import it.

from services.cloudconvert_service import CloudConvertService
import main

class TestCloudConvertService(unittest.IsolatedAsyncioTestCase):
    async def test_create_job(self):
        service = CloudConvertService("test_key")
        
        # Mock httpx.AsyncClient
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.__aenter__.return_value = mock_client
            
            # Use AsyncMock for post
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": {
                    "id": "job_123", 
                    "tasks": [
                        {"operation": "import/upload", "status": "waiting", "result": {"form": {"url": "http://upload", "parameters": {}}}}
                    ]
                }
            }
            mock_response.raise_for_status = MagicMock()
            
            # Since create_job makes 2 post calls (one for job create, one for upload)
            # We can use side_effect to return different responses if needed, but here simple return_value is enough 
            # as the first call returns the job json, and the second one (upload) return value is not used.
            # But wait, create_job implementation:
            # 1. client.post(jobs) -> returns job_response
            # 2. client.post(upload_url) -> returns upload_response (ignored)
            
            mock_client.post = AsyncMock(side_effect=[mock_response, MagicMock()])

            job_id = await service.create_job("dummy_path", "pdf", "http://webhook")
            
            self.assertEqual(job_id, "job_123")
            self.assertEqual(mock_client.post.call_count, 2)

    def test_extract_download_url(self):
        service = CloudConvertService("test_key")
        job_data = {
            "tasks": [
                {
                    "operation": "export/url", 
                    "status": "finished",
                    "result": {
                        "files": [{"url": "http://download.pdf", "filename": "output.pdf"}]
                    }
                }
            ]
        }
        url, filename = service.extract_download_url(job_data)
        self.assertEqual(url, "http://download.pdf")
        self.assertEqual(filename, "output.pdf")
        
    def test_extract_download_url_no_task(self):
        service = CloudConvertService("test_key")
        job_data = {"tasks": []}
        with self.assertRaises(ValueError):
            service.extract_download_url(job_data)

class TestVerification(unittest.TestCase):
    def test_verify_signature_valid(self):
        # We need to mock WEBHOOK_SECRET in main because it might be None if .env is missing/empty during test run
        # but modify_main.py (not shown) might have set it.
        # Let's set it manually
        main.WEBHOOK_SECRET = "secret"
        
        import hmac
        import hashlib
        
        payload = b'{"hello":"world"}'
        mac = hmac.new(b"secret", msg=payload, digestmod=hashlib.sha256)
        signature = mac.hexdigest()
        
        header = f"t=123456,v1={signature}"
        
        # Should not raise exception
        main.verify_signature(payload, header)

    def test_verify_signature_invalid(self):
        main.WEBHOOK_SECRET = "secret"
        payload = b'{"hello":"world"}'
        header = "t=123456,v1=wrong_signature"
        
        with self.assertRaises(HTTPException) as cm:
            main.verify_signature(payload, header)
        self.assertEqual(cm.exception.status_code, 403)

if __name__ == '__main__':
    print("Starting tests...")
    # Create dummy file for upload test
    with open("dummy_path", "w") as f:
        f.write("content")
    
    try:
        unittest.main(verbosity=2)
    finally:
        import os
        if os.path.exists("dummy_path"):
            os.remove("dummy_path")
