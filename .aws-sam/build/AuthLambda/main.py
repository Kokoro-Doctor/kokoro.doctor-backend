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
import json
from datetime import datetime, timedelta, timezone
import logging

# Initialize logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
doctors_table = dynamodb.Table(os.environ["DOCTORS_TABLE"])

# FastAPI app initialization
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

# Utility functions
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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
            "location": data.location
        })

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
            "onboarded": False
        })

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



