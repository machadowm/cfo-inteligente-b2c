import os
import json
import logging
import asyncio
from decimal import Decimal
import redis.asyncio as redis

# Configurações de infraestrutura
REDIS_URL = os.getenv("REDIS_URL", "redis://cfo_redis:6379/0")
logger = logging.getLogger(__name__)

class CustomJSONEncoder(json.JSONEncoder):
    """
    Encoder customizado para serialização de objetos Decimal em strings.
    Garante precisão em round-trips de dados financeiros para o cache.
    """
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)

class RedisFSMService:
    """
    Gerenciador de Estado Conversacional (FSM), Debouncing de mensagens 
    e Cache de Perfil com suporte a serialização de alta precisão.
    """
    _client = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_client(cls) -> redis.Redis:
        """
        Retorna a instância do cliente Redis (Singleton) com proteção contra race conditions.
        """
        if cls._client is None:
            async with cls._lock:
                if cls._client is None:
                    cls._client = redis.from_url(
                        REDIS_URL, 
                        decode_responses=True,
                        socket_timeout=5.0,
                        retry_on_timeout=True
                    )
        return cls._client

    @classmethod
    async def close_client(cls):
        """Encerrar a conexão do Singleton e reseta o atributo de classe."""
        async with cls._lock:
            if cls._client is not None:
                await cls._client.aclose()
                cls._client = None

    # --- MÉTODOS DE GERENCIAMENTO DE ESTADO (FSM) ---

    @staticmethod
    async def obter_estado(key: str) -> str:
        """Recupera o estado atual da FSM (prefixo state:) ou retorna IDLE."""
        client = await RedisFSMService.get_client()
        estado = await client.get(f"state:{key}")
        return estado if estado else "IDLE"

    @staticmethod
    async def definir_estado(key: str, estado: str, ex_seconds: int = 1800):
        """Grava o estado da FSM com expiração padrão de 30 minutos."""
        client = await RedisFSMService.get_client()
        await client.set(f"state:{key}", estado, ex=ex_seconds)

    @staticmethod
    async def limpar_buffer(key: str):
        """Remove de forma atômica o estado e o buffer de mensagens via pipeline."""
        client = await RedisFSMService.get_client()
        async with client.pipeline(transaction=True) as pipe:
            pipe.delete(f"state:{key}")
            pipe.delete(f"buffer_msg:{key}")
            await pipe.execute()

    # --- LÓGICA DE AGLUTINAÇÃO E DEBOUNCING ---

    @staticmethod
    async def acumular_mensagem(tenant_id: str, texto: str, janela_segundos: int = 4) -> list:
        """
        Acumula mensagens em uma lista no Redis e retorna o conjunto completo.
        Otimizado com pipeline para reduzir o RTT (Round Trip Time).
        """
        client = await RedisFSMService.get_client()
        list_key = f"buffer_msg:{tenant_id}"
        
        async with client.pipeline(transaction=True) as pipe:
            pipe.rpush(list_key, texto)
            pipe.expire(list_key, janela_segundos)
            pipe.lrange(list_key, 0, -1)
            results = await pipe.execute()
            
        # Retorna o resultado do comando LRANGE (terceiro comando do pipeline)
        return results[2] if len(results) > 2 else []

    # --- CONTROLE DE RESILIÊNCIA ---

    @staticmethod
    async def obter_erros_consecutivos(key: str) -> int:
        """Consulta o contador da chave errors: para o tenant."""
        client = await RedisFSMService.get_client()
        erros = await client.get(f"errors:{key}")
        return int(erros) if erros else 0

    @staticmethod
    async def incrementar_erros_consecutivos(key: str) -> int:
        """Incrementa o contador de erros com TTL de 15 minutos."""
        client = await RedisFSMService.get_client()
        key_name = f"errors:{key}"
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key_name)
            pipe.expire(key_name, 900)
            results = await pipe.execute()
        return results[0]

    @staticmethod
    async def limpar_erros_consecutivos(key: str):
        """Deleta a chave de erros do tenant."""
        client = await RedisFSMService.get_client()
        await client.delete(f"errors:{key}")

    # --- GERENCIAMENTO DE CACHE DE PERFIL ---

    @staticmethod
    async def registrar_audit_trava_zero(tenant_id: str, km: float):
        """Registra no Redis que a trava de faturamento zero foi acionada.

        Chave: audit_trava_zero:<tenant_id>  |  TTL: 7 dias.
        Cada chamada incrementa o contador e grava o timestamp e KM da última ocorrência.
        Isso permite identificar padrões de fechamento zerado (má-fé ou falha técnica).
        """
        import time
        client = await RedisFSMService.get_client()
        key = f"audit_trava_zero:{tenant_id}"
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.hset(f"{key}:meta", mapping={"ultimo_ts": str(int(time.time())), "ultimo_km": str(km)})
            pipe.expire(key, 604800)          # 7 dias
            pipe.expire(f"{key}:meta", 604800)
            await pipe.execute()

    @staticmethod
    async def cache_perfil_motorista(tenant_id: str, perfil_dict: dict, ex_seconds: int = 3600):
        """
        Serializa e armazena o perfil em cache (prefixo profile:).
        Utiliza CustomJSONEncoder para tratar campos Decimal oriundos do PostgreSQL.
        """
        client = await RedisFSMService.get_client()
        try:
            perfil_json = json.dumps(perfil_dict, cls=CustomJSONEncoder)
            await client.set(f"profile:{tenant_id}", perfil_json, ex=ex_seconds)
        except Exception as e:
            logger.error(f"Erro ao serializar perfil para cache (Tenant: {tenant_id}): {e}")

    @staticmethod
    async def obter_perfil_cache(tenant_id: str) -> dict:
        """Recupera e desserializa o perfil do motorista do cache."""
        client = await RedisFSMService.get_client()
        perfil_json = await client.get(f"profile:{tenant_id}")
        if not perfil_json:
            return None
            
        try:
            return json.loads(perfil_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Dados corrompidos no cache de perfil para {tenant_id}: {e}")
            return None
