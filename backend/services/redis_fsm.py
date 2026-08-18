import os
from typing import List, Optional

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


class RedisFSMService:
    """Gerencia estado conversacional e buffer transitório do FSM no Redis."""

    _client: Optional[redis.Redis] = None
    _DEFAULT_STATE = "IDLE"
    _STATE_TTL_SECONDS = 3600
    _MESSAGE_BUFFER_TTL_SECONDS = 60

    @classmethod
    async def init_redis(cls) -> None:
        if cls._client is not None:
            return

        cls._client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
        )

    @classmethod
    async def close_redis(cls) -> None:
        if cls._client is None:
            return

        await cls._client.aclose()
        cls._client = None

    @classmethod
    async def _get_client(cls) -> redis.Redis:
        if cls._client is None:
            await cls.init_redis()
        return cls._client

    @classmethod
    def _state_key(cls, flow_key: str) -> str:
        return f"fsm:state:{flow_key}"

    @classmethod
    def _buffer_key(cls, flow_key: str) -> str:
        return f"fsm:buffer:{flow_key}"

    @classmethod
    async def obter_estado(cls, flow_key: str) -> str:
        client = await cls._get_client()
        state = await client.get(cls._state_key(flow_key))
        return state if state else cls._DEFAULT_STATE

    @classmethod
    async def definir_estado(
        cls,
        flow_key: str,
        state: str,
        expire: int = _STATE_TTL_SECONDS,
    ) -> None:
        client = await cls._get_client()
        await client.set(cls._state_key(flow_key), state, ex=expire)

    @classmethod
    async def limpar_estado(cls, flow_key: str) -> None:
        client = await cls._get_client()
        await client.delete(cls._state_key(flow_key))

    @classmethod
    async def bufferizar_mensagem(cls, flow_key: str, message_text: str) -> None:
        client = await cls._get_client()
        key = cls._buffer_key(flow_key)

        async with client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, message_text)
            pipe.expire(key, cls._MESSAGE_BUFFER_TTL_SECONDS)
            await pipe.execute()

    @classmethod
    async def obter_buffer(cls, flow_key: str) -> List[str]:
        client = await cls._get_client()
        return await client.lrange(cls._buffer_key(flow_key), 0, -1)

    @classmethod
    async def limpar_buffer(cls, flow_key: str) -> None:
        client = await cls._get_client()
        await client.delete(cls._buffer_key(flow_key))

    @classmethod
    async def resetar_fluxo(cls, flow_key: str) -> None:
        await cls.limpar_estado(flow_key)
        await cls.limpar_buffer(flow_key)