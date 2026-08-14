import os
import asyncpg
from typing import Optional

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:strong_password_here@cfo_postgres:5432/cfo_b2c")

class DatabaseService:
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
            await cls._pool.release(connection)
