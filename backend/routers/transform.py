from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
import logging
from dependencies import get_current_user
from models import User
from openai import AsyncOpenAI
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/text", tags=["Text Transformation"])

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "sk-mock-key"))

class TransformRequest(BaseModel):
    action: str
    text: str
    target_language: str | None = None

class TransformResponse(BaseModel):
    success: bool
    result: str | None = None
    error: str | None = None

@router.post("/transform", response_model=TransformResponse)
async def transform_text(
    request: TransformRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Transforms text using AI. Currently supports 'translate' action.
    """
    if current_user.is_trial_expired and current_user.tier == "trial":
        logger.warning(f"User {current_user.id} attempted text transform but trial expired.")
        raise HTTPException(status_code=403, detail="Trial expired. Please upgrade.")

    if request.action != "translate":
        raise HTTPException(status_code=400, detail="Only 'translate' action is currently supported.")
        
    target_lang = request.target_language or "English"

    system_prompt = (
        "You are a professional translator.\n"
        f"Translate the provided text into {target_lang}.\n"
        "Preserve formatting, punctuation, line breaks, emojis, numbering, and bullet lists.\n"
        "Do not explain your work.\n"
        "Return only the translated text."
    )
    
    if os.getenv("ENVIRONMENT") == "development" and not os.getenv("OPENAI_API_KEY"):
         # Mock translation for dev
         import asyncio
         await asyncio.sleep(1)
         return TransformResponse(success=True, result=f"[Mock {target_lang}] {request.text}")

    try:
        chat_res = await client.chat.completions.create(
             model="gpt-4o",
             messages=[
                 {"role": "system", "content": system_prompt},
                 {"role": "user", "content": request.text}
             ]
         )
        result_text = chat_res.choices[0].message.content.strip()
        return TransformResponse(success=True, result=result_text)
    except Exception as e:
        logger.exception(f"Error transforming text for user {current_user.id}")
        return TransformResponse(success=False, error=str(e))
