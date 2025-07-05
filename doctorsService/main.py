from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from mangum import Mangum
import boto3
from boto3.dynamodb.conditions import Key
from typing import Optional, List
from enum import Enum
import base64
import mimetypes
import urllib.parse
import os
import logging



# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# environment variables
dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
doctors_table = dynamodb.Table(os.environ["DOCTORS_TABLE"])
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
availability_table = dynamodb.Table("DoctorAvailabilityTable")

s3 = boto3.client("s3")
BUCKET = os.getenv("S3_BUCKET", "kokoro-doctor-documents")

# ---- Init ----
app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

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


# ---- Models ----
class UploadDoc(BaseModel):
    filename: str
    base64_content: str

class DoctorProfileUpdate(BaseModel):
    email: EmailStr
    specialization: Optional[str] = None
    experience: Optional[str] = None
    fees: Optional[int] = None
    timings: Optional[str] = None
    licenseNumber: Optional[str] = None
    registrationId: Optional[str] = None
    affiliation: Optional[str] = None
    degreeCertificate: Optional[UploadDoc] = None
    govtIdProof: Optional[UploadDoc] = None
    profilePhoto: Optional[UploadDoc] = None 


class SubscribeRequest(BaseModel):
    user_email: EmailStr
    doctor_email: EmailStr

class AvailabilitySlot(BaseModel):
    start: str  # e.g. "09:00"
    end: str    # e.g. "09:30"

class WeekDay(str, Enum):
    Monday = "Monday"
    Tuesday = "Tuesday"
    Wednesday = "Wednesday"
    Thursday = "Thursday"
    Friday = "Friday"
    Saturday = "Saturday"
    Sunday = "Sunday"

class DoctorSlotsSetRequest(BaseModel):
    doctor_id: str
    day: WeekDay
    slots: List[AvailabilitySlot]

class DoctorSlotUpdateRequest(BaseModel):
    doctor_id: str
    day: WeekDay
    slot: AvailabilitySlot
    available: Optional[bool] = True  # default: enable


handler = Mangum(app)

# helper functions
# S3 upload helper
def upload_doc_to_s3(email, doc_type, filename, base64_content):
    try:
        key = f"doctors/{email}/{doc_type}/{urllib.parse.quote(filename)}"
        file_bytes = base64.b64decode(base64_content)

        # Guess content-type from filename (jpeg, png, pdf, etc.)
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type:
            content_type = "application/octet-stream"  # fallback

        logger.info(f"Uploading {key} with content_type {content_type}")

        s3.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )

        return f"https://{BUCKET}.s3.amazonaws.com/{key}"

    except Exception as e:
        logger.error(f"Error uploading {filename} to S3: {str(e)}")
        raise

# Generate presigned URL for S3 object
# This is useful for fetching documents later
# e.g. for displaying profile photos or documents
# in the frontend without making them public
def generate_presigned_url(key: str, expires_in=3600):
    try:
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=expires_in
        )
    except Exception as e:
        print("Presigned URL error:", e)
        return None
    

# ---- Routes ----
@app.post("/doctorsService/updateProfile")
def complete_doctor_profile(data: DoctorProfileUpdate):
    try:
        # Check doctor exists
        doctor = doctors_table.get_item(Key={"email": data.email})
        if "Item" not in doctor:
            raise HTTPException(status_code=404, detail="Doctor not found. Please sign up first.")

        update_expr_parts = []
        expr_values = {}

        # Optional S3 uploads
        doc_updates = {}
        if data.degreeCertificate:
            url = upload_doc_to_s3(
                email=data.email,
                doc_type="degreeCertificate",
                filename=data.degreeCertificate.filename,
                base64_content=data.degreeCertificate.base64_content
            )
            doc_updates["degreeCertificate"] = url
        if data.govtIdProof:
            url = upload_doc_to_s3(
                email=data.email,
                doc_type="govtIdProof",
                filename=data.govtIdProof.filename,
                base64_content=data.govtIdProof.base64_content
            )
            doc_updates["govtIdProof"] = url
        if data.profilePhoto:
            url = upload_doc_to_s3(
                email=data.email,
                doc_type="profilePhoto",
                filename=data.profilePhoto.filename,
                base64_content=data.profilePhoto.base64_content
            )
            doc_updates["profilePhoto"] = url

        # Add provided fields to update expression
        field_map = {
            "specialization": data.specialization,
            "experience": data.experience,
            "fees": data.fees,
            "timings": data.timings,
            "licenseNumber": data.licenseNumber,
            "registrationId": data.registrationId,
            "affiliation": data.affiliation
        }

        for key, val in field_map.items():
            if val is not None:
                placeholder = f":{key}"
                update_expr_parts.append(f"{key} = {placeholder}")
                expr_values[placeholder] = val

        # Add uploaded documents
        for idx, (key, val) in enumerate(doc_updates.items()):
            placeholder = f":doc{idx}"
            update_expr_parts.append(f"{key} = {placeholder}")
            expr_values[placeholder] = val

        # Always mark as onboarded
        update_expr_parts.append("onboarded = :ob")
        expr_values[":ob"] = True

        if not update_expr_parts:
            raise HTTPException(status_code=400, detail="No fields provided to update.")

        update_expr = "SET " + ", ".join(update_expr_parts)

        # Update DynamoDB
        doctors_table.update_item(
            Key={"email": data.email},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )

        return {"message": "Doctor profile updated successfully."}

    except HTTPException as he:
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# Fetch all doctors
# This endpoint returns all doctors with their S3 links converted to presigned URLs
# so that the frontend can access them without making the S3 objects public.
@app.post("/doctorsService/fetchDoctors")
def get_all_doctors():
    try:
        response = doctors_table.scan()
        doctors = response.get("Items", [])

        for doc in doctors:
            # Fields with S3 links
            for field in ["profilePhoto", "degreeCertificate", "govtIdProof"]:
                if field in doc and doc[field]:
                    try:
                        s3_key = doc[field].replace(f"https://{BUCKET}.s3.amazonaws.com/", "")
                        doc[field] = generate_presigned_url(s3_key)
                    except Exception:
                        doc[field] = None

        return {"doctors": doctors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Subscribe a user to a doctor
# It checks if the user and doctor exist, and if not already subscribed,
# it adds the user to the doctor's subscribers list and vice versa.
@app.post("/doctorsService/subscribe")
def subscribe_doctor(data: SubscribeRequest):
    try:
        # Fetch doctor
        doctor = doctors_table.get_item(Key={"email": data.doctor_email}).get("Item")
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        # Fetch user
        user = users_table.get_item(Key={"email": data.user_email}).get("Item")
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Safely get lists or default to empty
        current_subscribers = doctor.get("subscribers", [])
        current_subscribed_doctors = user.get("subscribed_doctors", [])

        # Check if already linked
        if data.user_email in current_subscribers or data.doctor_email in current_subscribed_doctors:
            return {"message": "User is already subscribed to this doctor."}
        
        # Add user to doctor's subscribers
        doctors_table.update_item(
            Key={"email": data.doctor_email},
            UpdateExpression="SET subscribers = list_append(if_not_exists(subscribers, :empty), :u)",
            ExpressionAttributeValues={
                ":u": [data.user_email],
                ":empty": [],
            },
        )

        # Add doctor to user's subscribed_doctors
        users_table.update_item(
            Key={"email": data.user_email},
            UpdateExpression="SET subscribed_doctors = list_append(if_not_exists(subscribed_doctors, :empty), :d)",
            ExpressionAttributeValues={
                ":d": [data.doctor_email],
                ":empty": [],
            },
        )

        return {"message": f"User {data.user_email} successfully subscribed to Doctor {data.doctor_email}"}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# set availability for a doctor
@app.post("/doctorsService/setSlots")
def set_availability(data: DoctorSlotsSetRequest):
    # Step 1: Check if doctor exists
    doctor = doctors_table.get_item(Key={"email": data.doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Step 2: Directly upsert each slot with 'available': True
    pk = f"{data.doctor_id}#{data.day}"

    for slot in data.slots:
        sk = f"{slot.start}-{slot.end}"
        availability_table.put_item(Item={
            "PK": pk,
            "SK": sk,
            "available": True
        })

    return {"message": f"Availability for {data.day} set successfully."}

# Update availability slot for a doctor
# This endpoint allows enabling or disabling a specific slot for a doctor on a given day.
@app.post("/doctorsService/updateSlot")
def update_slot(data: DoctorSlotUpdateRequest):
    pk = f"{data.doctor_id}#{data.day}"
    sk = f"{data.slot.start}-{data.slot.end}"

    try:
        availability_table.put_item(Item={
            "PK": pk,
            "SK": sk,
            "available": data.available  # 👈 true/false
        })
        return {"message": f"Slot {'enabled' if data.available else 'disabled'} successfully"}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

