import logging
import razorpay
import json
import os
import boto3
import datetime
from decimal import Decimal

# Initialize logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")))

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")
table_name = os.getenv("DYNAMODB_TABLE_NAME", "PaymentsTable")
table = dynamodb.Table(table_name)

# CORS Headers
HEADERS = {
    "Access-Control-Allow-Origin": "https://kokoro.doctor",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type"
}

def lambda_handler(event, context):
    try:
        logger.info(f"Event Received: {json.dumps(event)}")

        # Handle CORS Preflight requests
        if event.get("httpMethod") == "OPTIONS":
            return {
                "statusCode": 200,
                "headers": HEADERS,
                "body": json.dumps({"message": "CORS Preflight successful"})
            }

        if "body" in event:
            body = json.loads(event["body"])

            if "amount" in body and "payment_id" not in body:
                return create_payment_link(body, event)

            elif "payment_id" in body:
                return verify_payment(body, event)

        return {
            "statusCode": 400,
            "headers": HEADERS,
            "body": json.dumps({"error": "Invalid request"})
        }

    except Exception as e:
        logger.error(f"Exception: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps({"error": str(e)})
        }

# ✅ Step 1: Create Razorpay Payment Link
def create_payment_link(body, event):
    try:
        logger.info(f"Creating payment link with body: {body}")
        amount = body.get("amount")

        if not amount or not isinstance(amount, (int, float)) or amount <= 0:
            return {
                "statusCode": 400,
                "headers": HEADERS,
                "body": json.dumps({"error": "Invalid amount"})
            }

        amount_in_paise = int(amount * 100)

        payment_link_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "description": "Payment for selected plan",
            "callback_url": "https://kokoro.doctor/payment-success",
            "callback_method": "get"
        }

        payment_link = razorpay_client.payment_link.create(payment_link_data)

        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({
                "message": "Payment link created successfully",
                "payment_link": payment_link["short_url"]
            })
        }

    except Exception as e:
        logger.error(f"Exception in create_payment_link: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps({"error": str(e)})
        }

# ✅ Step 2: Verify Payment & Store in DynamoDB
def verify_payment(body, event):
    try:
        logger.info(f"Verifying payment with body: {body}")
        payment_id = body.get("payment_id")

        if not payment_id:
            return {
                "statusCode": 400,
                "headers": HEADERS,
                "body": json.dumps({"error": "Missing payment_id"})
            }

        # Fetch payment details from Razorpay
        payment_details = razorpay_client.payment.fetch(payment_id)
        order_id = payment_details.get("order_id", "N/A")
        status = payment_details.get("status", "failed")
        amount = payment_details.get("amount", 0)

        # Generate Invoice if payment is successful
        invoice_url = None
        if status == "captured":
            invoice_url = generate_invoice_url(payment_id)

        # Store Transaction Data in DynamoDB
        payment_data = {
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": Decimal(amount) / Decimal(100),
            "currency": "INR",
            "status": status,
            "timestamp": str(datetime.datetime.utcnow()),
            "invoice_url": invoice_url
        }
        table.put_item(Item=payment_data)

        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({
                "message": "Payment processed",
                "order_id": order_id,
                "payment_id": payment_id,
                "status": status,
                "invoice_url": invoice_url
            })
        }

    except Exception as e:
        logger.error(f"Exception in verify_payment: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps({"error": str(e)})
        }

# ✅ Step 3: Generate Invoice URL
def generate_invoice_url(payment_id):
    base_url = "https://yourwebsite.com/invoices"
    return f"{base_url}/{payment_id}.pdf"