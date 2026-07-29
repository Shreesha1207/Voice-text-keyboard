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
from rate_limit import limit_by_identity

logger = logging.getLogger(__name__)

# A person dictating continuously manages roughly one clip every few seconds, so
# this is far above real use — it exists to bound cost if a token is abused.
TRANSCRIBE_PER_MINUTE = 30

# Upload ceiling. ~10 minutes of the 16 kHz mono WAV the client sends.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

router = APIRouter(prefix="/api/transcribe", tags=["Transcription"])

@router.post("", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    session_id: str = Form(None),
    language: str = Form("en"),
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

    # Premium Feature: Only paid users can choose language. 
    # Force trial users to English regardless of what the client sent.
    if current_user.tier != "paid":
        language = "en"

    await limit_by_identity(
        "transcribe", str(current_user.id), limit=TRANSCRIBE_PER_MINUTE, window_seconds=60
    )

    audio_bytes = await file.read()

    # Reject oversized uploads before they reach disk or the transcription API.
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        logger.warning(
            f"User {current_user.id} sent {len(audio_bytes)} bytes, over the "
            f"{MAX_AUDIO_BYTES} limit."
        )
        raise HTTPException(
            status_code=413,
            detail="Recording is too long. Please keep clips under about 10 minutes.",
        )

    priority = Priority.PAID if current_user.tier == "paid" else Priority.TRIAL
    
    # 1. Enqueue
    enqueue_result = await queue_manager.enqueue_request(
        audio_bytes, 
        priority, 
        language=language,
        translate=current_user.is_translation_enabled if current_user.tier == "paid" else False
    )
    
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
        if result["error"] == "timeout":
            raise HTTPException(status_code=504, detail="Transcription timed out. Please try again.")
        raise HTTPException(status_code=502, detail="Transcription failed. Please try again.")

    # Convert to response
    wait_time = result.get("queue_wait", 0)
    audio_duration = result.get("audio_duration", 0)

    # Nothing was said — a mis-tap of the hotkey, or speech too quiet for the voice
    # activity filter. Return an empty result rather than tripping the word_count > 0
    # validation rule (which surfaced to the user as an unhandled HTTP 500).
    if result["word_count"] == 0:
        return TranscribeResponse(
            text="", word_count=0, char_count=0, wpm=None,
            queue_wait_ms=int(wait_time * 1000)
        )

    # WPM must be derived from how long the user spoke, not from how long our server
    # took to transcribe. internal_record_stats computes it from the audio duration
    # when wpm is None, which also matches how /stats/summary reports peak WPM.
    wpm = round(result["word_count"] / (audio_duration / 60.0), 2) if audio_duration else None

    # 3. Automatically record stats to database
    await internal_record_stats(
        user=current_user,
        db=db,
        data=RecordWordsRequest(
            word_count=result["word_count"],
            char_count=result["char_count"],
            wpm=None,
            session_id=session_id,
            audio_duration_seconds=audio_duration
        )
    )

    return TranscribeResponse(
        text=result["text"],
        word_count=result["word_count"],
        char_count=result["char_count"],
        wpm=wpm,
        queue_wait_ms=int(wait_time * 1000)
    )
