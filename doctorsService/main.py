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
    specialization: str
    experience: str
    fees: int
    timings: str
    licenseNumber: Optional[str] = None
    registrationId: Optional[str] = None
    affiliation: Optional[str] = None
    degreeCertificate: Optional[UploadDoc] = None
    govtIdProof: Optional[UploadDoc] = None

class SubscribeRequest(BaseModel):
    user_email: EmailStr
    doctor_email: EmailStr

class AvailabilitySlot(BaseModel):
    start: str  # e.g. "10:00"
    end: str    # e.g. "10:30"


class WeekDay(str, Enum):
    Monday = "Monday"
    Tuesday = "Tuesday"
    Wednesday = "Wednesday"
    Thursday = "Thursday"
    Friday = "Friday"
    Saturday = "Saturday"
    Sunday = "Sunday"

class AvailabilityInput(BaseModel):
    doctor_id: str
    day: WeekDay
    slots: List[AvailabilitySlot]


handler = Mangum(app)

# helper functions
# S3 upload helper
def upload_doc_to_s3(email, doc_type, filename, base64_content):
    key = f"doctors/{email}/{doc_type}/{filename}"
    file_bytes = base64.b64decode(base64_content)
    s3.put_object(Bucket=BUCKET, Key=key, Body=file_bytes)
    return f"https://{BUCKET}.s3.amazonaws.com/{key}"


# ---- Routes ----
@app.post("/doctorsService/completeProfile")
def complete_doctor_profile(data: DoctorProfileUpdate):
    try:
        # Check doctor exists
        doctor = doctors_table.get_item(Key={"email": data.email})
        if "Item" not in doctor:
            raise HTTPException(status_code=404, detail="Doctor not found. Please sign up first.")

        # Docs are ALWAYS optional now
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

        # Prepare UpdateExpression
        update_expr = """
            SET specialization = :spec, experience = :exp,
                fees = :fees, timings = :timings, licenseNumber = :license,
                registrationId = :regId, affiliation = :aff, onboarded = :ob
        """
        expr_values = {
            ":spec": data.specialization,
            ":exp": data.experience,
            ":fees": data.fees,
            ":timings": data.timings,
            ":license": data.licenseNumber,
            ":regId": data.registrationId,
            ":aff": data.affiliation,
            ":ob": True  # Always mark as onboarded
        }

        #  Add docs if uploaded
        for idx, (key, val) in enumerate(doc_updates.items()):
            placeholder = f":doc{idx}"
            update_expr += f", {key} = {placeholder}"
            expr_values[placeholder] = val

        #  Update DynamoDB
        doctors_table.update_item(
            Key={"email": data.email},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )

        return {"message": "Doctor profile completed successfully."}

    except HTTPException as he:
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/doctorsService/fetchDoctors")
def get_all_doctors():
    try:
        response = doctors_table.scan()
        doctors = response.get("Items", [])
        return {"doctors": doctors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
def set_availability(data: AvailabilityInput):
    # Step 1: Check if doctor exists
    doctor = doctors_table.get_item(Key={"email": data.doctor_id})
    if "Item" not in doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Step 2: Delete existing slots for that doctor and day
    pk = f"{data.doctor_id}#{data.day}"
    existing = availability_table.query(
        KeyConditionExpression=Key("PK").eq(pk)
    )
    for item in existing.get("Items", []):
        availability_table.delete_item(Key={"PK": pk, "SK": item["SK"]})

    # Step 3: Add new slots
    for slot in data.slots:
        availability_table.put_item(Item={
            "PK": pk,
            "SK": f"{slot.start}-{slot.end}"
        })

    return {"message": f"Availability for {data.day} set successfully."}
