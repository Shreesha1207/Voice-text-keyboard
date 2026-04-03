import redis.asyncio as redis
import os
import json
import asyncio
import logging
from enum import IntEnum
import uuid

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

class Priority(IntEnum):
    PAID = 1
    TRIAL = 2

class QueueManager:
    def __init__(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self.channel_prefix = "transcribe_result:"
        # Limits
        self.limits = {
             Priority.PAID: {"slots": 20},
             Priority.TRIAL: {"slots": 50}
        }

    async def enqueue_request(self, audio_content: bytes, priority: Priority) -> dict:
        """
        Pushes a request into the appropriate queue and waits for the result via pub/sub.
        In a real microservices, the worker would be a separate process.
        Here we will emulate it returning just a 'job_id'.
        """
        queue_key = f"queue:{priority.name.lower()}"
        
        # Check limits
        current_len = await self.redis.llen(queue_key)
        if current_len >= self.limits[priority]["slots"]:
            return {"error": "queue_full", "wait_estimate": current_len * 2}
            
        job_id = str(uuid.uuid4())
        
        # We store the raw audio temporarily
        # For a production system we'd use S3/Blob storage, but for now we write temp files 
        # or store the binary locally on the disk, telling the worker the path.
        # Since this backend runs the worker too right now, we can just save it.
        temp_dir = os.path.join(os.getcwd(), "temp_audio")
        os.makedirs(temp_dir, exist_ok=True)
        filename = f"{job_id}.wav"
        filepath = os.path.join(temp_dir, filename)
        
        with open(filepath, "wb") as f:
             f.write(audio_content)

        job_data = {
            "job_id": job_id,
            "priority": priority.value,
            "filepath": filepath,
            "enqueued_at": asyncio.get_event_loop().time()
        }
        
        await self.redis.rpush(queue_key, json.dumps(job_data))
        
        return {"job_id": job_id, "filepath": filepath}
        
    async def wait_for_result(self, job_id: str, timeout: int = 30):
        """Wait for the worker to publish the transcription back."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"{self.channel_prefix}{job_id}")
        
        try:
             async def _listen():
                  async for message in pubsub.listen():
                       if message["type"] == "message":
                            return json.loads(message["data"])
             return await asyncio.wait_for(_listen(), timeout=timeout)
        except asyncio.TimeoutError:
             logger.warning(f"Timeout waiting for transcription result for job {job_id}")
             return {"error": "timeout", "text": ""}
        finally:
             await pubsub.unsubscribe()
             
queue_manager = QueueManager()
