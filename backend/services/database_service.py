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
        """Busca o veículo principal ativo associado ao tenant do motorista sob isolamento RLS.
        Inclui flags escalares de motorização (is_hibrido, is_eletrico, is_flex, capacidade_bateria)
        que têm precedência sobre os campos equivalentes em estoque_financeiro.meta.
        """
        async with cls.get_tenant_connection(motorista_id) as conn:
            return await conn.fetchrow(
                """
                SELECT id, placa, modelo, tipo_combustivel, estoque_financeiro,
                       locadora, custo_aluguel_semanal, franquia_km_semanal,
                       valor_km_excedente, escala_trabalho, contrato_personalizado,
                       is_hibrido, is_eletrico, is_flex, capacidade_bateria,
                       selecionado
                FROM public.veiculos
                WHERE motorista_id = $1::uuid AND ativo = TRUE
                ORDER BY selecionado DESC, created_at DESC LIMIT 1;
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
                # Insere ou recupera o motorista de forma idempotente.
                # ON CONFLICT (telefone) atualiza o nome para refletir correções de cadastro.
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

                # Cria o veículo inicial. ON CONFLICT (placa) para onboarding idempotente.
                # selecionado=TRUE: primeiro veículo sempre é o ativo selecionado.
                # O índice único parcial idx_veiculo_selecionado_unico garante exclusividade.
                await conn.execute(
                    """
                    INSERT INTO public.veiculos (motorista_id, modelo, placa, tipo_combustivel, estoque_financeiro, selecionado)
                    VALUES ($1::uuid, $2, $3, $4, $5::jsonb, TRUE)
                    ON CONFLICT (placa) DO NOTHING;
                    """,
                    motorista_id, veiculo_modelo, placa, combustivel,
                    '{"meta": {"tipo_veiculo": "gasolina", "is_flex": false, "is_hibrido": false, "is_eletrico": false, "capacidade_tanque_l": 50.0, "capacidade_bateria_kwh": 0.0, "qtd_tanques": 1}, "liquido": {"litros": 0.0, "custo_total": 0.0, "gasolina_litros": 0.0, "etanol_litros": 0.0, "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0, "km_l_gasolina": 12.0, "km_l_etanol": 8.5}, "eletricidade": {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5}, "gnv": {"m3": 0.0, "custo_total": 0.0, "km_m3": 14.0}}'
                )

                # Cria a caixa de manutenção padrão (universal — vale para qualquer tipo de contrato).
                # A caixa "Amortização de IPVA/Seguro" é criada pelo sincronizar_despesa_contrato
                # apenas para carros próprios/financiados — não faz sentido em carros alugados.
                await conn.execute(
                    """
                    INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual)
                    VALUES ($1::uuid, 'Manutenção Corretiva (Pneus/Freios)', 0.00)
                    ON CONFLICT (motorista_id, nome_caixa) DO NOTHING;
                    """,
                    motorista_id
                )
                return motorista_id

    @classmethod
    async def cadastrar_veiculo_adicional(
        cls,
        motorista_id: str,
        modelo: str,
        placa: str,
        combustivel: str,
        estoque_jsonb: str,
    ) -> str:
        """
        Cadastra um veículo adicional para um motorista já existente.

        Operação atômica (transação única):
          1. Verifica que não há turno aberto (proteção de integridade).
          2. Desseleciona todos os veículos ativos do motorista.
          3. Insere o novo veículo com selecionado=TRUE e ativo=TRUE.
             ON CONFLICT (placa) atualiza estoque e reativa, tornando-o o selecionado.
          4. O índice único parcial idx_veiculo_selecionado_unico garante exclusividade.

        Retorna o UUID do novo veículo como string.
        Lança ValueError se houver turno aberto.
        """
        async with cls.get_tenant_connection(motorista_id) as conn:
            # Proteção: não cadastra com turno em aberto
            turno_aberto = await conn.fetchval(
                "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid "
                "AND status IN ('em_andamento', 'em_pausa', 'ABERTO') LIMIT 1;",
                motorista_id,
            )
            if turno_aberto:
                raise ValueError("Há um turno em aberto. Feche o turno antes de cadastrar um novo veículo.")

            # Desseleciona todos (dois passos para evitar violação transitória do índice único)
            await conn.execute(
                "UPDATE public.veiculos SET selecionado = FALSE "
                "WHERE motorista_id = $1::uuid AND ativo = TRUE;",
                motorista_id,
            )

            # Insere ou reativa veículo existente por placa
            veiculo_id = await conn.fetchval(
                """
                INSERT INTO public.veiculos
                    (motorista_id, modelo, placa, tipo_combustivel, estoque_financeiro, ativo, selecionado)
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, TRUE, TRUE)
                ON CONFLICT (placa) DO UPDATE
                    SET motorista_id       = EXCLUDED.motorista_id,
                        modelo             = EXCLUDED.modelo,
                        tipo_combustivel   = EXCLUDED.tipo_combustivel,
                        estoque_financeiro = EXCLUDED.estoque_financeiro,
                        ativo              = TRUE,
                        selecionado        = TRUE
                RETURNING id;
                """,
                motorista_id, modelo, placa, combustivel, estoque_jsonb,
            )
            return str(veiculo_id)

