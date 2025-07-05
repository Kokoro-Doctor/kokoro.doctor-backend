from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from mangum import Mangum
from typing import List
from datetime import datetime, timedelta
import boto3, os, logging
from boto3.dynamodb.conditions import Key

app = FastAPI()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
availability_table = dynamodb.Table("DoctorAvailabilityTable")
booking_table = dynamodb.Table("DoctorBookingsTable")

# --------- Models ---------
class BookSlotInput(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD
    start: str  # HH:MM
    user_id: str

class CancelSlotInput(BookSlotInput):
    pass

class AvailableSlotsRequest(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD

class FetchBookingsRequest(BaseModel):
    id: str           # email of user or doctor
    type: str         # "user" or "doctor"
    days: int         # number of days to look back



# --------- API Endpoints ---------
@app.post("/doctorBookings/book")
def book_slot(data: BookSlotInput):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"

    try:
        # Step 1: Check if slot is available in availability table
        date_obj = datetime.strptime(data.date, "%Y-%m-%d")
        day_of_week = date_obj.strftime("%A")  # e.g., "Monday"
        availability_pk = f"{data.doctor_id}#{day_of_week}"
        availability_sk = None

        # Fetch availability
        res = availability_table.query(KeyConditionExpression=Key("PK").eq(availability_pk))
        found_slot = False
        for item in res.get("Items", []):
            start_time = item["SK"].split("-")[0]
            if start_time == data.start and item.get("available", True):
                availability_sk = item["SK"]
                found_slot = True
                break

        if not found_slot:
            raise HTTPException(status_code=400, detail="Slot is unavailable or does not exist.")

        # Step 2: Check if user has already booked that slot
        check_existing = booking_table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(data.start)
        )
        for item in check_existing.get("Items", []):
            if item["user_id"] == data.user_id:
                raise HTTPException(status_code=400, detail="User already booked this slot.")

        # Step 3: Proceed with booking
        expiry = int((date_obj + timedelta(days=7)).timestamp())
        booking_table.put_item(Item={
            "PK": pk,
            "SK": sk,
            "doctor_id": data.doctor_id,
            "date": data.date,
            "start": data.start,
            "user_id": data.user_id
        })

        # Step 4: Mark slot as unavailable in availability table
        availability_table.put_item(Item={
            "PK": availability_pk,
            "SK": availability_sk,
            "available": False
        })

        return {"message": "Slot booked successfully."}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception("Booking failed")
        raise HTTPException(500, detail=str(e))


# Cancel a booked slot
@app.post("/doctorBookings/cancel")
def cancel_slot(data: CancelSlotInput):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"

    try:
        booking_table.delete_item(Key={"PK": pk, "SK": sk})

        # Make slot available again
        date_obj = datetime.strptime(data.date, "%Y-%m-%d")
        day_of_week = date_obj.strftime("%A")
        availability_pk = f"{data.doctor_id}#{day_of_week}"
        availability_sk = None

        # Find the slot key from availability
        res = availability_table.query(KeyConditionExpression=Key("PK").eq(availability_pk))
        for item in res.get("Items", []):
            if item["SK"].split("-")[0] == data.start:
                availability_sk = item["SK"]
                break

        if availability_sk:
            availability_table.put_item(Item={
                "PK": availability_pk,
                "SK": availability_sk,
                "available": True
            })

        return {"message": "Booking cancelled and slot released."}
    except Exception as e:
        logger.exception("Cancellation failed")
        raise HTTPException(500, detail=str(e))


# Fetch available slots for a doctor on a specific date
# This endpoint returns all available slots for a doctor on a given date.
# It first converts the date to the corresponding day of the week, then queries the
# DoctorAvailabilityTable to get the slots for that day.
@app.post("/doctorBookings/available")
def get_available_slots(data: AvailableSlotsRequest):
    try:
        # Step 1: Convert date to day of week
        date_obj = datetime.strptime(data.date, "%Y-%m-%d")
        day = date_obj.strftime("%A")

        # Step 2: Fetch slots from DoctorAvailabilityTable
        availability_pk = f"{data.doctor_id}#{day}"
        res = availability_table.query(KeyConditionExpression=Key("PK").eq(availability_pk))
        slots = res.get("Items", [])

        # Step 3: Format result
        result = []
        for slot in slots:
            start, end = slot["SK"].split("-")
            result.append({
                "start": start,
                "end": end,
                "available": slot.get("available", True)
            })

        return {"slots": result}
    except Exception as e:
        logger.exception("Failed to fetch available slots")
        raise HTTPException(500, detail=str(e))


# Fetch bookings for a doctor or user
# type: "doctor" for doctor bookings, "user" for user bookings
@app.post("/doctorBookings/fetchBookings")
def fetch_bookings(data: FetchBookingsRequest):
    today = datetime.utcnow().date()
    all_bookings = []

    try:
        if data.type == "doctor":
            # Scan by partition key for last N days
            for i in range(data.days):
                date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                pk = f"{data.id}#{date}"
                res = booking_table.query(KeyConditionExpression=Key("PK").eq(pk))
                all_bookings.extend(res.get("Items", []))

        elif data.type == "user":
            res = booking_table.query(
                IndexName="GSI_UserBookings",
                KeyConditionExpression=Key("user_id").eq(data.id)
            )
            for item in res.get("Items", []):
                booking_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
                if (today - booking_date).days <= data.days:
                    all_bookings.append(item)

        else:
            raise HTTPException(status_code=400, detail="Invalid type: must be 'doctor' or 'user'")

        return {"bookings": all_bookings}

    except Exception as e:
        logger.exception("Failed to fetch bookings")
        raise HTTPException(status_code=500, detail=str(e))


# --------- AWS Lambda Handler ---------
handler = Mangum(app)