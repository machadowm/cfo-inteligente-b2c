import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")


class DatabaseService:
    """Gerencia o pool PostgreSQL e conexões com escopo transacional de tenant."""

    _pool: Optional[asyncpg.Pool] = None

    @classmethod
    def _get_database_url(cls) -> str:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL não configurada. Defina a variável de ambiente antes de iniciar a aplicação."
            )
        return DATABASE_URL

    @classmethod
    async def initialize_pool(cls) -> None:
        if cls._pool is not None:
            return

        cls._pool = await asyncpg.create_pool(
            dsn=cls._get_database_url(),
            min_size=2,
            max_size=20,
            command_timeout=60.0,
            max_queries=50_000,
            max_inactive_connection_lifetime=300.0,
        )

    @classmethod
    async def close_pool(cls) -> None:
        if cls._pool is None:
            return

        await cls._pool.close()
        cls._pool = None

    @classmethod
    def _ensure_pool(cls) -> asyncpg.Pool:
        if cls._pool is None:
            raise RuntimeError(
                "Pool não inicializado. Chame initialize_pool() no startup da aplicação."
            )
        return cls._pool

    @classmethod
    @asynccontextmanager
    async def get_connection(cls) -> AsyncIterator[asyncpg.Connection]:
        pool = cls._ensure_pool()
        async with pool.acquire() as connection:
            yield connection

    @classmethod
    @asynccontextmanager
    async def get_tenant_connection(
        cls,
        motorista_id: str,
    ) -> AsyncIterator[asyncpg.Connection]:
        pool = cls._ensure_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.current_driver_id', $1, true)",
                    motorista_id,
                )
                yield connection

    @classmethod
    async def buscar_motorista_por_telefone(cls, telefone: str):
        async with cls.get_connection() as conn:
            return await conn.fetchrow(
                """
                SELECT id, nome, telefone
                FROM motoristas
                WHERE telefone = $1
                LIMIT 1
                """,
                telefone,
            )

    @classmethod
    async def buscar_veiculo_ativo_do_motorista(cls, motorista_id: str):
        async with cls.get_tenant_connection(motorista_id) as conn:
            return await conn.fetchrow(
                """
                SELECT id, placa, modelo
                FROM veiculos
                WHERE motorista_id = $1::uuid
                ORDER BY criado_em DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                motorista_id,
            )