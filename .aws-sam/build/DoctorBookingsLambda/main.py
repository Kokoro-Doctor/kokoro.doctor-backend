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

class UserBookingsRequest(BaseModel):
    user_id: str


# --------- API Endpoints ---------
@app.post("/doctorBookings/book")
def book_slot(data: BookSlotInput):
    pk = f"{data.doctor_id}#{data.date}"
    sk = f"{data.start}#{data.user_id}"
    key = {"PK": pk, "SK": sk}

    try:
        # Check slot users
        res = booking_table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(data.start)
        )
        users = [item["user_id"] for item in res.get("Items", [])]

        if data.user_id in users:
            raise HTTPException(400, detail="User already booked")
        if len(users) >= 5:
            raise HTTPException(400, detail="Slot full")

        expiry = int((datetime.strptime(data.date, "%Y-%m-%d") + timedelta(days=7)).timestamp())

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



@app.post("/doctorBookings/user")
def get_user_bookings(data: UserBookingsRequest):
    today = datetime.utcnow().date()
    res = booking_table.query(
        IndexName="GSI_UserBookings",
        KeyConditionExpression=Key("user_id").eq(data.user_id)
    )
    items = [
        item for item in res.get("Items", [])
        if (today - datetime.strptime(item["date"], "%Y-%m-%d").date()).days <= 7
    ]
    return {"bookings": items}


# --------- AWS Lambda Handler ---------
handler = Mangum(app)