from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import logging

from dependencies import get_current_user
from database import get_db
from models import User
from schemas import TranscribeResponse, RecordWordsRequest
from routers.stats import internal_record_stats
from queue_manager import queue_manager, Priority

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transcribe", tags=["Transcription"])

@router.post("", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    session_id: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Receives audio bytes, puts them in the correct priority queue, limits trials, 
    and waits for the background worker to finish and return text.
    """
    
    if current_user.is_trial_expired and current_user.tier == "trial":
        logger.warning(f"User {current_user.id} attempted transcription but trial expired.")
        raise HTTPException(status_code=403, detail="Trial expired. Please upgrade.")

    audio_bytes = await file.read()
    
    priority = Priority.PAID if current_user.tier == "paid" else Priority.TRIAL
    
    # 1. Enqueue
    enqueue_result = await queue_manager.enqueue_request(audio_bytes, priority)
    
    if "error" in enqueue_result:
        logger.error(f"Failed to enqueue transcription request: {enqueue_result['error']} for user {current_user.id}")
        # e.g., queue_full
        raise HTTPException(
             status_code=429, 
             detail=f"Server busy. Evaluated wait: {enqueue_result['wait_estimate']}s"
        )
        
    job_id = enqueue_result["job_id"]
    
    # 2. Wait for result via pub/sub timeout (fallback safety)
    result = await queue_manager.wait_for_result(job_id, timeout=45)
    
    if "error" in result:
        logger.error(f"Transcription error for job {job_id} (user {current_user.id}): {result['error']}")
        raise HTTPException(status_code=504, detail="Transcription timed out.")

    # Convert to response
    wait_time = result.get("queue_wait", 0)
    wpm = None
    processing_time = result.get("processing_time", 0)
    if processing_time and processing_time > 0:
        wpm = round((result["word_count"] / processing_time) * 60, 2)

    # 3. Automatically record stats to database
    await internal_record_stats(
        user=current_user,
        db=db,
        data=RecordWordsRequest(
            word_count=result["word_count"],
            char_count=result["char_count"],
            wpm=wpm,
            session_id=session_id
        )
    )

    return TranscribeResponse(
        text=result["text"],
        word_count=result["word_count"],
        char_count=result["char_count"],
        wpm=wpm,
        queue_wait_ms=int(wait_time * 1000)
    )
