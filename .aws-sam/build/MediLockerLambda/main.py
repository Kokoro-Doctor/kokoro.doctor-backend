from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import boto3
import os
import base64
import logging
from mangum import Mangum

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# FastAPI app instance
app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
# Custom exception handler for HTTP exceptions
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": "https://kokoro.doctor",
            "Access-Control-Allow-Credentials": "true"
        }
    )

# AWS S3 client setup
s3 = boto3.client("s3")
BUCKET = os.getenv("S3_BUCKET", "s3-medilocker")

# Request models
class FileUploadModel(BaseModel):
    filename: str
    content: str
    metadata: Optional[Dict[str, str]] = {}

class UploadRequest(BaseModel):
    email: str
    files: List[FileUploadModel]

class EmailRequest(BaseModel):
    email: str

class FileRequest(BaseModel):
    email: str
    filename: str


@app.post("/medilocker/upload")
async def upload_file(body: UploadRequest):
    try:
        email = body.email
        files = body.files

        existing_files = set()
        s3_response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{email}/")
        if "Contents" in s3_response:
            existing_files = {obj["Key"].split("/")[-1] for obj in s3_response["Contents"]}

        for file in files:
            if file.filename in existing_files:
                raise HTTPException(status_code=409, detail="File with the same name already exists")
            try:
                file_binary = base64.b64decode(file.content)
            except base64.binascii.Error:
                raise HTTPException(status_code=400, detail="Invalid base64 encoding")

            s3.put_object(
                Bucket=BUCKET,
                Key=f"{email}/{file.filename}",
                Body=file_binary,
                Metadata={k: str(v) for k, v in file.metadata.items()}
            )

        return {"message": "Files uploaded successfully"}
    except Exception as e:
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/medilocker/fetch")
async def fetch_files(body: EmailRequest):
    try:
        email = body.email
        s3_response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{email}/")
        files = s3_response.get('Contents', [])
        if not files:
            return {"message": "No files found", "files": []}

        files_info = []
        for obj in files:
            key = obj['Key']
            filename = key.split(f"{email}/", 1)[1]
            try:
                head_response = s3.head_object(Bucket=BUCKET, Key=key)
                metadata = head_response.get("Metadata", {})
            except Exception:
                metadata = {}

            files_info.append({"filename": filename, "metadata": metadata})

        return {"files": files_info}
    except Exception as e:
        logger.exception("Fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/medilocker/download")
async def generate_download_link(body: FileRequest):
    try:
        file_key = f"{body.email}/{body.filename}"
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": BUCKET, "Key": file_key},
            ExpiresIn=3600
        )
        return {"download_url": url}
    except Exception as e:
        logger.exception("Presigned URL generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/medilocker/delete")
async def delete_file(body: FileRequest):
    try:
        file_key = f"{body.email}/{body.filename}"
        try:
            s3.head_object(Bucket=BUCKET, Key=file_key)
        except s3.exceptions.ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise HTTPException(status_code=404, detail="File not found")
            raise

        s3.delete_object(Bucket=BUCKET, Key=file_key)
        return {"message": "File deleted successfully"}
    except Exception as e:
        logger.exception("Deletion failed")
        raise HTTPException(status_code=500, detail=str(e))


# Attach Mangum handler for AWS Lambda
handler = Mangum(app)
