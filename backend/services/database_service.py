import os
import asyncpg
from typing import Optional
from contextlib import asynccontextmanager

# Obtém a string de conexão das variáveis de ambiente do container/servidor
DATABASE_URL = os.getenv("DATABASE_URL")


class DatabaseService:
<<<<<<< HEAD
    _pool: Optional[asyncpg.Pool] = None

    @classmethod
    async def initialize_pool(cls):
        """Inicializa o pool de conexões assíncronas do PostgreSQL."""
        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=20,
                command_timeout=60.0
            )

    @classmethod
    async def close_pool(cls):
        """Fecha o pool de conexões de forma segura (shutdown)."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None

    @classmethod
    async def obter_conexao(cls) -> asyncpg.Connection:
        """Adquire uma conexão ativa a partir do pool."""
        if cls._pool is None:
            await cls.initialize_pool()
        return await cls._pool.acquire()

    @classmethod
    async def liberar_conexao(cls, connection: asyncpg.Connection):
        """Devolve a conexão ao pool."""
        if cls._pool is not None:
=======
    """Serviço de gerenciamento do pool de conexões PostgreSQL com suporte a RLS (Multi-Tenant)."""

    _pool: Optional[asyncpg.Pool] = None

    @classmethod
    async def initialize_pool(cls):
        """Inicializa o pool de conexões assíncronas do PostgreSQL no arranque do FastAPI."""
        if not DATABASE_URL:
            raise ValueError(
                "ERRO CRÍTICO: A variável de ambiente DATABASE_URL não foi configurada!"
            )

        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(
                DATABASE_URL, min_size=2, max_size=20, command_timeout=60.0
            )
            print("Pool de conexões PostgreSQL inicializado com sucesso.")

    @classmethod
    async def close_pool(cls):
        """Encerra todas as conexões do pool no shutdown do FastAPI."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            print("Pool de conexões PostgreSQL encerrado com sucesso.")

    @classmethod
    @asynccontextmanager
    async def get_tenant_connection(cls, tenant_id: str):
        """Garante a aquisição de uma conexão isolada por Tenant via Row-Level Security (RLS).

        Aplica SET LOCAL para definir o tenant_id e RESET no fechamento para
        evitar vazamento de estado entre conexões reaproveitadas do pool.
        """
        if cls._pool is None:
            await cls.initialize_pool()

        # Requisita uma conexão disponível no pool
        connection = await cls._pool.acquire()

        try:
            # Injeta o ID do motorista na sessão da conexão (Ativa o RLS do Postgres)
            await connection.execute(
                "SET LOCAL app.current_tenant_id = $1", tenant_id
            )
            yield connection
        finally:
            # Limpa as configurações locais do tenant antes de devolver a conexão ao pool
            await connection.execute("RESET app.current_tenant_id")
>>>>>>> b6763d0 (Atualização dos arquivos do projeto CFO Inteligente B2C)
            await cls._pool.release(connection)
