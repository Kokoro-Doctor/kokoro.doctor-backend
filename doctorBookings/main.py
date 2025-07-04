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
        # Step 0: Validate slot exists in availability
        date_obj = datetime.strptime(data.date, "%Y-%m-%d")
        day_of_week = date_obj.strftime("%A")  # e.g., "Monday"
        availability_pk = f"{data.doctor_id}#{day_of_week}"

        availability_res = availability_table.query(KeyConditionExpression=Key("PK").eq(availability_pk))
        available_slots = [item["SK"].split("-")[0] for item in availability_res.get("Items", [])]

        if data.start not in available_slots:
            raise HTTPException(status_code=400, detail="Selected slot is not available")

        # Step 1: Check if user already booked or slot is full
        res = booking_table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(data.start)
        )
        users = [item["user_id"] for item in res.get("Items", [])]

        if data.user_id in users:
            raise HTTPException(400, detail="User already booked")
        if len(users) >= 5:
            raise HTTPException(400, detail="Slot full")

        # Step 2: Save booking
        expiry = int((date_obj + timedelta(days=7)).timestamp())
        booking_table.put_item(Item={
            "PK": pk,
            "SK": sk,
            "doctor_id": data.doctor_id,
            "date": data.date,
            "start": data.start,
            "user_id": data.user_id,
            "ttl": expiry
        })
        return {"message": "Slot booked"}
    
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception("Booking failed")
        raise HTTPException(500, detail=str(e))



@app.post("/doctorBookings/cancel")
def cancel_slot(data: CancelSlotInput):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"
    try:
        booking_table.delete_item(Key={"PK": pk, "SK": sk})
        return {"message": "Booking cancelled"}
    except Exception as e:
        logger.exception("Cancellation failed")
        raise HTTPException(500, detail=str(e))


@app.post("/doctorBookings/available")
def get_available_slots(data: AvailableSlotsRequest):
    # Convert date to day of the week
    date_obj = datetime.strptime(data.date, "%Y-%m-%d")
    day = date_obj.strftime("%A")  # e.g., "Monday"

    # Step 1: Get default slots for that day
    availability_pk = f"{data.doctor_id}#{day}"
    res = availability_table.query(KeyConditionExpression=Key("PK").eq(availability_pk))
    default_slots = res.get("Items", [])

    # Step 2: Get booked users for that specific date
    booking_pk = f"{data.doctor_id}#{data.date}"
    booked_res = booking_table.query(KeyConditionExpression=Key("PK").eq(booking_pk))
    booked = {}
    for item in booked_res.get("Items", []):
        time = item["SK"].split('#')[0]
        booked.setdefault(time, []).append(item["user_id"])

    # Step 3: Merge availability with bookings
    result = []
    for slot in default_slots:
        start, end = slot["SK"].split("-")
        users = booked.get(start, [])
        result.append({
            "start": start,
            "end": end,
            "booked": len(users),
            "available": 5 - len(users)
        })
    return {"slots": result}

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