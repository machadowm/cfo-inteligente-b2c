import os
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://cfo_redis:6379/0")

class RedisFSMService:
    _pool = None

    @classmethod
    def get_redis(cls):
        if cls._pool is None:
            cls._pool = redis.from_url(REDIS_URL, decode_responses=True)
        return cls._pool

    @staticmethod
    async def obter_estado(fsm_key: str) -> str:
        r = RedisFSMService.get_redis()
        estado = await r.get(fsm_key)
        return estado if estado else "IDLE"

    @staticmethod
    async def definir_estado(fsm_key: str, estado: str, ex_seconds: int = 1800):
        r = RedisFSMService.get_redis()
        await r.set(fsm_key, estado, ex=ex_seconds)

    @staticmethod
    async def limpar_buffer(fsm_key: str):
        r = RedisFSMService.get_redis()
        await r.delete(fsm_key)
