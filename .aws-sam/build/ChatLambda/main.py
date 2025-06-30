from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
import boto3
import os
import json
import requests
from datetime import datetime, timezone
import logging
import re

# Configure Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS & Environment Config
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE")
OLLAMA_API = os.getenv("OLLAMA_API")
CERT_PATH = "kokoro.doctor.fullchain.pem"

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)

# FastAPI App Initialization
app = FastAPI()

origins = ["https://kokoro.doctor"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

class ChatRequest(BaseModel):
    user_id: str
    message: str
    language: Optional[str] = "en"


def store_message(user_mail, user_message, bot_message):
    try:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        table.put_item(
            Item={
                "email": user_mail,
                "timestamp": timestamp,
                "user_message": json.dumps({"role": "user", "text": user_message}),
                "bot_message": json.dumps({"role": "bot", "text": bot_message}),
            }
        )
        logger.info(f"Stored message for user: {user_mail}")
    except Exception as e:
        logger.error(f"Error storing message: {e}", exc_info=True)


def get_chat_history(user_mail):
    try:
        response = table.query(
            KeyConditionExpression="email = :email_value",
            ExpressionAttributeValues={":email_value": user_mail},
            ScanIndexForward=False,
            Limit=5,
        )
        messages = response.get("Items", [])
        history = []
        for item in reversed(messages):
            user_msg = json.loads(item["user_message"])
            bot_msg = json.loads(item["bot_message"])
            history.append(f"user: {user_msg.get('text', '')}")
            history.append(f"bot: {bot_msg.get('text', '')}")
        return history
    except Exception as e:
        logger.error(f"Error fetching chat history: {e}", exc_info=True)
        return []


def call_llm_api(history, user_question, language="en"):
    context = "\n".join(history) if history else "No prior messages."
    prompt = f"""
    You are a friendly, caring, and empathetic heart health assistant developed by Metafied.  
    Your role is to provide clear, concise, and actionable heart health advice.  

    Guidelines:  
    - Keep responses brief (1-2 sentences), focusing on clarity, medications, diet, and actionability.  
    - Respond to heart symptoms with lifestyle, medication, or dietary suggestions.  
    - Gently steer unrelated topics back to heart health.  
    - Always give helpful recommendations and note when medical attention is needed.  
    - Support: English, Hindi, Spanish, Telugu.  

    Context: {context}  
    User ({language}): {user_question}  
    AI Response ({language} - Concise & Clear):  
    """
    try:
        payload = {
            "model": "llama3",
            "prompt": prompt,
            "stream": False,
            "language": language
        }
        res = requests.post(f"{OLLAMA_API}/api/generate", json=payload, headers={"Content-Type": "application/json"}, verify=CERT_PATH)
        if res.status_code != 200:
            logger.error(f"Ollama API Error: {res.status_code}, {res.reason}\n{res.content.decode(errors='ignore')}")
            raise HTTPException(status_code=502, detail="AI response error")
        res = res.json().get("response", "No response from model.")
        return res
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="LLM request failed")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Unknown error")


@app.post("/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    history = get_chat_history(request.user_id)
    ai_response = call_llm_api(history, request.message, request.language)
    store_message(request.user_id, request.message, ai_response)
    return {"text": ai_response}


handler = Mangum(app)