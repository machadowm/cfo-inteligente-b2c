import os
import json
import redis.asyncio as redis
from typing import Optional, Any

REDIS_URL = os.getenv("REDIS_URL", "redis://cfo_redis:6379/0")

class RedisFSMService:
    _client = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            cls._client = redis.from_url(REDIS_URL, decode_responses=True)
        return cls._client

    @staticmethod
    async def obter_estado(key: str) -> str:
        client = await RedisFSMService.get_client()
        estado = await client.get(f"state:{key}")
        return estado if estado else "IDLE"

    @staticmethod
    async def definir_estado(key: str, estado: str, ex_seconds: int = 1800):
        client = await RedisFSMService.get_client()
        await client.set(f"state:{key}", estado, ex=ex_seconds)

    @staticmethod
    async def limpar_buffer(key: str):
        client = await RedisFSMService.get_client()
        await client.delete(f"state:{key}")
        await client.delete(f"buffer_msg:{key}")

    @staticmethod
    async def cache_perfil_motorista(tenant_id: str, dados: dict, ex: int = 300):
        client = await RedisFSMService.get_client()
        await client.set(f"profile:{tenant_id}", json.dumps(dados), ex=ex)

    @staticmethod
    async def obter_perfil_cache(tenant_id: str) -> Optional[dict]:
        client = await RedisFSMService.get_client()
        dados = await client.get(f"profile:{tenant_id}")
        return json.loads(dados) if dados else None

    @staticmethod
    async def incrementar_erros_consecutivos(key: str) -> int:
        client = await RedisFSMService.get_client()
        erros = await client.incr(f"errors:{key}")
        await client.expire(f"errors:{key}", 900)
        return erros

    @staticmethod
    async def limpar_erros_consecutivos(key: str):
        client = await RedisFSMService.get_client()
        await client.delete(f"errors:{key}")

    @classmethod
    async def close_client(cls):
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None
