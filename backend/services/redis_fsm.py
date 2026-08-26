import os
import json
import redis.asyncio as redis
from typing import Optional, Any

REDIS_URL = os.getenv("REDIS_URL", "redis://cfo_redis:6379/0")

class RedisFSMService:
    """
    Gerenciador de Estado Conversacional (Finite State Machine) e Aglutinação de Mensagens (Debouncing) via Redis 7.
    Adiciona controle de erros consecutivos para prevenção de loops infinitos e cache de perfil de baixa latência.
    """
    _client = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            cls._client = redis.from_url(REDIS_URL, decode_responses=True)
        return cls._client

    @staticmethod
    async def obter_estado(key: str) -> str:
        """Obtém o estado atual da FSM para o tenant."""
        client = await RedisFSMService.get_client()
        estado = await client.get(f"state:{key}")
        return estado if estado else "IDLE"

    @staticmethod
    async def definir_estado(key: str, estado: str, ex_seconds: int = 1800):
        """Define o estado atual da FSM com TTL de segurança (padrão 30 minutos)."""
        client = await RedisFSMService.get_client()
        await client.set(f"state:{key}", estado, ex=ex_seconds)

    @staticmethod
    async def limpar_buffer(key: str):
        """Limpa o estado e os buffers associados à FSM do tenant."""
        client = await RedisFSMService.get_client()
        await client.delete(f"state:{key}")
        await client.delete(f"buffer_msg:{key}")

    @staticmethod
    async def cache_perfil_motorista(tenant_id: str, dados: dict, ex: int = 300):
        """Armazena metadados do motorista por 5 minutos para evitar I/O no Postgres."""
        client = await RedisFSMService.get_client()
        await client.set(f"profile:{tenant_id}", json.dumps(dados), ex=ex)

    @staticmethod
    async def obter_perfil_cache(tenant_id: str) -> Optional[dict]:
        """Recupera metadados do cache."""
        client = await RedisFSMService.get_client()
        dados = await client.get(f"profile:{tenant_id}")
        return json.loads(dados) if dados else None

    @staticmethod
    async def acumular_mensagem(tenant_id: str, texto: str, janela_segundos: int = 4) -> list:
        """
        Implementa o debouncing: acumula mensagens enviadas em rajada pelo motorista numa lista do Redis, renovando a janela temporal.
        Retorna o acumulado.
        """
        client = await RedisFSMService.get_client()
        list_key = f"buffer_msg:{tenant_id}"
        async with client.pipeline(transaction=True) as pipe:
            pipe.rpush(list_key, texto)
            pipe.expire(list_key, janela_segundos)
            await pipe.execute()
        mensagens = await client.lrange(list_key, 0, -1)
        return mensagens

    @staticmethod
    async def obter_erros_consecutivos(key: str) -> int:
        """Obtém o contador de erros consecutivos para o tenant."""
        client = await RedisFSMService.get_client()
        erros = await client.get(f"errors:{key}")
        return int(erros) if erros else 0

    @staticmethod
    async def incrementar_erros_consecutivos(key: str) -> int:
        """Incrementa o contador de erros consecutivos para o tenant e renova o TTL de 15 minutos."""
        client = await RedisFSMService.get_client()
        erros = await client.incr(f"errors:{key}")
        await client.expire(f"errors:{key}", 900)
        return erros

    @staticmethod
    async def limpar_erros_consecutivos(key: str):
        """Reseta o contador de erros consecutivos do tenant."""
        client = await RedisFSMService.get_client()
        await client.delete(f"errors:{key}")

    @classmethod
    async def close_client(cls):
        """Encerra o cliente Redis e reseta o singleton para permitir reconexão limpa."""
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None
