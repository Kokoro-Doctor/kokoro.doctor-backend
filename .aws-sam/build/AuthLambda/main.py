from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional
from mangum import Mangum
import os
import bcrypt
import jwt
import boto3
import uuid
from uuid import uuid4
import json
from datetime import datetime, timedelta, timezone
import logging
import smtplib
from email.message import EmailMessage

# Initialize logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
doctors_table = dynamodb.Table(os.environ["DOCTORS_TABLE"])
auth_tokens_table = dynamodb.Table(os.environ["AUTH_TOKENS_TABLE"])

smtp_user = os.environ["BREVO_SMTP_USER"]
smtp_key = os.environ["BREVO_SMTP_KEY"]
smtp_server = os.environ["BREVO_SMTP_SERVER"]
smtp_port = int(os.environ["BREVO_SMTP_PORT"])  # convert to int


# FastAPI app initialization
app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor", "http://localhost:8081"],
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

# Schemas
class UserSignup(BaseModel):
    username: str
    email: EmailStr
    password: str
    phoneNumber: str
    location: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class DoctorSignup(BaseModel):
    doctorname: str
    email: EmailStr
    phoneNumber: str
    password: str
    location: Optional[str] = None

class DoctorLogin(BaseModel):
    email: EmailStr
    password: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str
    new_password: str


# Utility functions
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def send_brevo_email(to_email: str, subject: str, body: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "verify@kokoro.doctor"
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_key)
            smtp.send_message(msg)
            logger.info(f"[Email] Sent to {to_email}")
    except Exception as e:
        logger.error(f"[Email] Failed to send to {to_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to send email")


# Mangum handler
handler = Mangum(app)

# Routes
@app.post("/auth/user/signup")
def user_signup(data: UserSignup):
    logger.info(f"[UserSignup] Received data: {data}")
    try:
        existing = users_table.get_item(Key={"email": data.email})
        logger.info(f"[UserSignup] DynamoDB get_item result: {existing}")

        if existing.get("Item"):
            raise HTTPException(status_code=400, detail="Email already exists")

        hashed = hash_password(data.password)
        logger.info(f"[UserSignup] Hashed password generated")

        users_table.put_item(Item={
            "username": data.username,
            "email": data.email,
            "password": hashed,
            "phoneNumber": data.phoneNumber,
            "location": data.location,
            "emailVerified": False
        })

        # Create token
        token = str(uuid.uuid4())

        auth_tokens_table.put_item(Item={
            "email": data.email,
            "purpose": "email_verification",
            "token": token,
            "ttl": int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp())
        })

        # Send verification email
        verification_link = f"https://kokoro.doctor/verify-email?token={token}&email={data.email}"
        subject = "Verify your email for Kokoro Doctor"
        body = f"Click the link to verify your email: {verification_link}"
        send_brevo_email(data.email, subject, body)

        logger.info(f"[UserSignup] Verification email sent to {data.email}")
        logger.info(f"[UserSignup] User {data.email} registered successfully")
        return {"message": "User registered successfully"}

    except HTTPException as he:
        logger.warning(f"[UserSignup] {he.detail}")
        raise he
    except Exception as e:
        logger.exception("[UserSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/user/login")
def user_login(data: UserLogin):
    logger.info(f"[UserLogin] Received login data for {data.email}")
    try:
        user = users_table.get_item(Key={"email": data.email}).get("Item")
        if not user:
            logger.warning(f"[UserLogin] User not found: {data.email}")
            raise HTTPException(status_code=400, detail="User not found")

        if not verify_password(data.password, user["password"]):
            logger.warning(f"[UserLogin] Incorrect password for {data.email}")
            raise HTTPException(status_code=400, detail="Incorrect password")

        logger.info(f"[UserLogin] Login successful for {data.email}")
        return {
            "user": {
                "name": user["username"],
                "email": user["email"]
            }
        }

    except HTTPException as he:
        logger.warning(f"[UserLogin] {he.detail}")
        raise he
    except Exception as e:
        logger.exception("[UserLogin] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/doctor/signup")
def doctor_signup(data: DoctorSignup):
    logger.info(f"[DoctorSignup] Received signup data: {data}")
    try:
        existing = doctors_table.get_item(Key={"email": data.email})
        logger.info(f"[DoctorSignup] DynamoDB get_item result: {existing}")

        if existing.get("Item"):
            raise HTTPException(status_code=400, detail="Email already registered")

        hashed = hash_password(data.password)
        logger.info(f"[DoctorSignup] Hashed password generated")

        doctors_table.put_item(Item={
            "email": data.email,
            "doctorname": data.doctorname,
            "phoneNumber": data.phoneNumber,
            "location": data.location,
            "password": hashed,
            "emailVerified": False,
            "onboarded": False
        })

        # Create token
        token = str(uuid.uuid4())

        auth_tokens_table.put_item(Item={
            "email": data.email,
            "purpose": "email_verification",
            "token": token,
            "ttl": int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp())
        })

        # Send verification email
        verification_link = f"https://kokoro.doctor/verify-email?token={token}&email={data.email}"
        subject = "Verify your email for Kokoro Doctor"
        body = f"Click the link to verify your email: {verification_link}"
        send_brevo_email(data.email, subject, body)

        logger.info(f"[DoctorSignup] Verification email sent to {data.email}")
        logger.info(f"[DoctorSignup] Doctor {data.email} registered successfully")
        return {"message": "Doctor registered. Please complete profile setup."}

    except HTTPException as he:
        logger.warning(f"[DoctorSignup] {he.detail}")
        raise he
    except Exception as e:
        logger.exception("[DoctorSignup] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/doctor/login")
def doctor_login(data: DoctorLogin):
    logger.info(f"[DoctorLogin] Received login data for {data.email}")
    try:
        doctor = doctors_table.get_item(Key={"email": data.email}).get("Item")
        if not doctor:
            logger.warning(f"[DoctorLogin] Doctor not found: {data.email}")
            raise HTTPException(status_code=400, detail="Doctor not found")

        if not verify_password(data.password, doctor["password"]):
            logger.warning(f"[DoctorLogin] Incorrect password for {data.email}")
            raise HTTPException(status_code=400, detail="Incorrect password")

        logger.info(f"[DoctorLogin] Login successful for {data.email}")
        return {
            "doctor": {
                "name": doctor["name"],
                "email": doctor["email"]
            }
        }

    except HTTPException as he:
        logger.warning(f"[DoctorLogin] {he.detail}")
        raise he
    except Exception as e:
        logger.exception("[DoctorLogin] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


# Email verification endpoint
@app.post("/auth/verify")
def verify_email(email: EmailStr, token: str):
    try:
        # Fetch using composite key: email + purpose
        res = auth_tokens_table.get_item(Key={
            "email": email,
            "purpose": "email_verification"
        })
        item = res.get("Item")

        if not item or item["token"] != token:
            raise HTTPException(status_code=400, detail="Invalid or expired token")

        # Update verification status
        user = users_table.get_item(Key={"email": email}).get("Item")
        if user:
            users_table.update_item(
                Key={"email": email},
                UpdateExpression="SET emailVerified = :v",
                ExpressionAttributeValues={":v": True}
            )

        doctor = doctors_table.get_item(Key={"email": email}).get("Item")
        if doctor:
            doctors_table.update_item(
                Key={"email": email},
                UpdateExpression="SET emailVerified = :v",
                ExpressionAttributeValues={":v": True}
            )

        if not user and not doctor:
            raise HTTPException(status_code=404, detail="Email not found in any table")

        logger.info(f"[VerifyEmail] Email {email} verified successfully")

        # Delete token after use
        auth_tokens_table.delete_item(Key={
            "email": email,
            "purpose": "email_verification"
        })

        return {"message": "Email verified successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception("[VerifyEmail] Error")
        raise HTTPException(status_code=500, detail=str(e))
    

# Password reset request endpoint
@app.post("/auth/request-password-reset")
def request_password_reset(data: PasswordResetRequest):
    # Check if email exists in users or doctors table
    user = users_table.get_item(Key={"email": data.email}).get("Item")
    doctor = doctors_table.get_item(Key={"email": data.email}).get("Item")

    if not user and not doctor:
        raise HTTPException(status_code=404, detail="Email not registered")

    token = str(uuid4())
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)

    auth_tokens_table.put_item(Item={
        "email": data.email,
        "purpose": "password_reset",
        "token": token,
        "ttl": int(expires.timestamp())
    })

    # Send reset email (link to frontend)
    reset_link = f"https://kokoro.doctor/reset-password?email={data.email}&token={token}"

    # NEW BREVO EMAIL
    subject = "Reset Your Password"
    body = f"Click to reset your password: {reset_link}"
    send_brevo_email(data.email, subject, body)

    logger.info(f"[RequestPasswordReset] Password reset link sent to {data.email}")
    return {"message": "Password reset link sent to your email"}


# Password reset confirmation endpoint
@app.post("/auth/reset-password")
def reset_password(data: PasswordResetConfirm):
    # Fetch token entry
    res = auth_tokens_table.get_item(Key={"email": data.email, "purpose": "password_reset"})
    item = res.get("Item")

    if not item or item["token"] != data.token:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    # Hash new password
    hashed = hash_password(data.new_password)

    # Update password in users or doctors table
    if users_table.get_item(Key={"email": data.email}).get("Item"):
        users_table.update_item(
            Key={"email": data.email},
            UpdateExpression="SET password = :p",
            ExpressionAttributeValues={":p": hashed}
        )
    elif doctors_table.get_item(Key={"email": data.email}).get("Item"):
        doctors_table.update_item(
            Key={"email": data.email},
            UpdateExpression="SET password = :p",
            ExpressionAttributeValues={":p": hashed}
        )
    else:
        raise HTTPException(404, detail="Email not found")

    # Delete token
    auth_tokens_table.delete_item(Key={"email": data.email, "purpose": "password_reset"})

    return {"message": "Password reset successfully"}
