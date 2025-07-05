from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import boto3
import uuid
from mangum import Mangum
import logging

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize FastAPI and CORS
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


# AWS Configuration
#S3_BUCKET_NAME = "kokoro-doctor-documents"
S3_BUCKET_NAME = "doctorFiles3Bucker" #s3-Bucket Name
DYNAMO_TABLE_NAME = "DoctorRegistrationsDB" # DynamoDB Table Name

s3_client = boto3.client("s3")
dynamo_resource = boto3.resource("dynamodb")
dynamo_table = dynamo_resource.Table(DYNAMO_TABLE_NAME)

# Pydantic Model (for docs)
class DoctorRegistration(BaseModel):
    medical_license_no: str
    document_url: str
    specialization: str
    year_of_experience: int
    affiliated_hospital_clinic: str
    # created_at: Optional[str]

# Upload Documents to S3
def upload_file_to_s3(file: UploadFile) -> str:
    try:
        file_ext = file.filename.split(".")[-1]
        key = f"documents/{uuid.uuid4()}.{file_ext}"

        s3_client.upload_fileobj(file.file, S3_BUCKET_NAME, key)
        s3_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{key}"
        return s3_url
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"S3 Uploading Process Failed: {str(e)}")

# Save Data to DynamoDB
def save_to_dynamodb(data: dict) -> str:
    try:
        doctor_id = str(uuid.uuid4())
        item = {
            "doctor_id": doctor_id,
            **data,
            "created_at": datetime.utcnow().isoformat()
        }
        dynamo_table.put_item(Item=item)
        return doctor_id
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB Save Failed: {str(e)}")

# POST Route: Register Doctor
@app.post("/register-doctor")
async def register_doctor(
    medical_license_no: str = Form(...),
    specialization: str = Form(...),
    year_of_experience: int = Form(...),
    affiliated_hospital_clinic: str = Form(""),
    document: UploadFile = File(...)
):
    # 1. Upload document to S3
    document_url = upload_file_to_s3(document)

    # 2. Prepare data for DB
    doctor_data = {
        "medical_license_no": medical_license_no,
        "document_url": document_url,
        "specialization": specialization,
        "year_of_experience": year_of_experience,
        "affiliated_hospital_clinic": affiliated_hospital_clinic
    }

    # 3. Save to DynamoDB
    doctor_id = save_to_dynamodb(doctor_data)

    return JSONResponse({
        "message": "Doctor Data Saved successfully.",
        "doctor_id": doctor_id
    })


handler = Mangum(app)
