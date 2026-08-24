import os
import logging
import asyncpg
from typing import Optional, AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://admin:strong_password_here@cfo_postgres:5432/cfo_b2c"
)

class DatabaseService:
    """
    Gerenciador de Conexões e Pool de Conexões assíncronas PostgreSQL (asyncpg).
    Aplica isolamento Multi-Tenant em nível de banco de dados (Row-Level Security)
    utilizando a variável de sessão 'app.current_driver_id'.
    """
    _pool: Optional[asyncpg.Pool] = None

    @classmethod
    async def initialize_pool(cls):
        """Inicializa o pool de conexões com codec nativo de UUID para strings do Python."""
        if cls._pool is None:
            try:
                cls._pool = await asyncpg.create_pool(
                    dsn=DATABASE_URL,
                    min_size=5,
                    max_size=30,
                    command_timeout=30.0,
                    max_inactive_connection_lifetime=300.0,
                    init=lambda conn: conn.set_type_codec(
                        'uuid', encoder=str, decoder=str, schema='pg_catalog'
                    )
                )
                logger.info("[DatabaseService] Connection pool PostgreSQL (asyncpg) inicializado com sucesso.")
            except Exception as e:
                logger.critical(f"[DatabaseService] Falha catastrófica ao inicializar o pool do PostgreSQL: {e}")
                raise

    @classmethod
    async def close_pool(cls):
        """Drena e encerra graciosamente todas as conexões ativas do pool (teardown)."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logger.info("[DatabaseService] Pool de conexões PostgreSQL encerrado.")

    @classmethod
    def _ensure_pool(cls) -> asyncpg.Pool:
        if cls._pool is None:
            raise RuntimeError("Pool de banco de dados não inicializado. Chame initialize_pool() primeiro.")
        return cls._pool

    @classmethod
    @asynccontextmanager
    async def get_connection(cls) -> AsyncIterator[asyncpg.Connection]:
        """Context manager de conexão básica sem injeção de escopo de tenant."""
        pool = cls._ensure_pool()
        async with pool.acquire() as conn:
            yield conn

    @classmethod
    @asynccontextmanager
    async def get_tenant_connection(cls, motorista_id: str) -> AsyncIterator[asyncpg.Connection]:
        """
        BLINDAGEM MULTI-TENANT (SRE Enterprise Grade):
        Adquire uma conexão do pool e inicia uma transação atômica injetando a variável
        de sessão local 'app.current_driver_id'. O PostgreSQL aplica automaticamente as
        políticas de RLS. Se a transação falhar, o rollback é executado e a variável é resetada.
        """
        pool = cls._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Injeta a credencial lógica no escopo local da transação (set_config nativo em C)
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true);", motorista_id)
                try:
                    yield conn
                finally:
                    # Garantia de limpeza (RESET ALL) pós-transação para evitar contaminação do socket no pool
                    try:
                        await conn.execute("RESET ALL;")
                    except Exception:
                        pass

    @classmethod
    async def buscar_motorista_por_telefone(cls, telefone: str) -> Optional[asyncpg.Record]:
        """Resolve os dados cadastrais básicos de um motorista com base em seu telefone."""
        async with cls.get_connection() as conn:
            tel_limpo = ''.join(filter(str.isdigit, telefone))
            # Use the last 11 digits (full Brazilian mobile number: DDD + 9 digits) to avoid
            # cross-tenant collisions that a shorter suffix match would allow.
            return await conn.fetchrow(
                """
                SELECT id, telefone, nome, meta_mensal_faturamento, dias_uteis_mes, nome_social, possui_multiplos_veiculos
                FROM public.motoristas
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(telefone, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '') LIKE '%' || $1
                LIMIT 1;
                """,
                tel_limpo[-11:]
            )

    @classmethod
    async def buscar_veiculo_ativo_do_motorista(cls, motorista_id: str) -> Optional[asyncpg.Record]:
        """Busca o veículo principal ativo associado ao tenant do motorista sob isolamento RLS."""
        async with cls.get_tenant_connection(motorista_id) as conn:
            return await conn.fetchrow(
                """
                SELECT id, placa, modelo, tipo_combustivel, estoque_financeiro, locadora,
                       custo_aluguel_semanal, franquia_km_semanal, valor_km_excedente, escala_trabalho,
                       is_flex, qtd_tanques, is_hibrido, is_eletrico, capacidade_tanque, capacidade_bateria,
                       contrato_personalizado
                FROM public.veiculos
                WHERE motorista_id = $1::uuid AND ativo = TRUE
                ORDER BY created_at DESC LIMIT 1;
                """,
                motorista_id
            )

    @classmethod
    async def registrar_novo_motorista(
        cls, 
        telefone: str, 
        nome: str, 
        veiculo_modelo: str, 
        combustivel: str, 
        placa: str
    ) -> str:
        """
        Registra de forma atômica (UPSERT) o motorista e seu respectivo veículo inicial
        durante o onboarding conversacional, prevenindo condições de corrida.
        """
        async with cls.get_connection() as conn:
            async with conn.transaction():
                # Insere ou recupera o motorista de forma idempotente
                row_motorista = await conn.fetchrow(
                    """
                    INSERT INTO public.motoristas (telefone, nome)
                    VALUES ($1, $2)
                    ON CONFLICT (telefone) DO UPDATE SET nome = EXCLUDED.nome
                    RETURNING id;
                    """,
                    telefone, nome
                )
                motorista_id = str(row_motorista["id"])

                # Cria o veículo inicial associado ao motorista recém-criado
                # Configurando uma matriz de estoque financeiro compatível com múltiplos reservatórios
                await conn.execute(
                    """
                    INSERT INTO public.veiculos (motorista_id, modelo, placa, tipo_combustivel, estoque_financeiro)
                    VALUES ($1::uuid, $2, $3, $4, $5::jsonb)
                    ON CONFLICT DO NOTHING;
                    """,
                    motorista_id, veiculo_modelo, placa, combustivel,
                    '{"liquido": {"litros": 0.0, "custo_total": 0.0, "gasolina_litros": 0.0, "etanol_litros": 0.0, "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0, "km_l_gasolina": 12.0, "km_l_etanol": 8.5}, "eletricidade": {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5}}'
                )

                # Cria caixinhas de provisões básicas para o motorista de forma automática
                await conn.execute(
                    """
                    INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual)
                    VALUES 
                        ($1::uuid, 'Manutenção Corretiva (Pneus/Freios)', 0.00),\n                        ($1::uuid, 'Amortização de IPVA/Seguro', 0.00)
                    ON CONFLICT DO NOTHING;
                    """,
                    motorista_id
                )
                return motorista_id
