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

    async def enqueue_request(self, audio_content: bytes, priority: Priority, language: str = "en", translate: bool = False) -> dict:
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
            "language": language,
            "translate": translate,
            "enqueued_at": asyncio.get_event_loop().time()
        }
        
        # Subscribe BEFORE making the job visible to a worker. Once the rpush lands
        # a worker can pop, process and publish immediately; anything published
        # before this subscription exists is lost for good.
        pubsub = await self.subscribe_for_result(job_id)

        await self.redis.rpush(queue_key, json.dumps(job_data))

        return {"job_id": job_id, "filepath": filepath, "pubsub": pubsub}
        
    async def subscribe_for_result(self, job_id: str):
        """Register interest in a job's result BEFORE it is enqueued.

        Redis pub/sub has no persistence: a message published while nobody is
        subscribed is discarded permanently. Subscribing only after enqueueing left
        a window in which a fast result — a rejected API key, a missing file, any
        immediate failure — was published to nobody, so the request hung for the
        full timeout and then returned a 504 the client silently ignored.
        """
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"{self.channel_prefix}{job_id}")
        return pubsub

    async def wait_for_result(self, job_id: str, timeout: int = 30, pubsub=None):
        """Wait for the worker to publish the transcription back.

        Pass the pubsub returned by subscribe_for_result() to close the race. When
        omitted it subscribes here, preserving the old behaviour for any caller
        that has not been updated.
        """
        own_subscription = pubsub is None
        if own_subscription:
            pubsub = await self.subscribe_for_result(job_id)

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
             try:
                 await pubsub.unsubscribe()
                 await pubsub.close()
             except Exception:
                 pass
             
queue_manager = QueueManager()
