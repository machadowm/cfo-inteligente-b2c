import os
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

class RedisFSMService:
    _client: redis.Redis = None

    @classmethod
    async def init_redis(cls):
        if not cls._client:
            cls._client = redis.from_url(REDIS_URL, decode_responses=True)

    @classmethod
    async def close_redis(cls):
        if cls._client:
            await cls._client.close()
            cls._client = None

    @classmethod
    async def get_state(cls, driver_id: str) -> str:
        if not cls._client:
            await cls.init_redis()
        state = await cls._client.get(f"fsm:state:{driver_id}")
        return state if state else "IDLE"

    @classmethod
    async def set_state(cls, driver_id: str, state: str, expire: int = 3600):
        if not cls._client:
            await cls.init_redis()
        await cls._client.set(f"fsm:state:{driver_id}", state, ex=expire)

    @classmethod
    async def buffer_message(cls, driver_id: str, message_text: str):
        if not cls._client:
            await cls.init_redis()
        key = f"buffer:msg:{driver_id}"
        
        # Pipeline atómico: Insere no final da lista e renova o tempo de expiração do debouncing
        async with cls._client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, message_text)
            pipe.expire(key, 60)
            await pipe.execute()
