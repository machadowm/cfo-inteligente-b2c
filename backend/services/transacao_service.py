<<<<<<< HEAD
import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional
from services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class TransacaoService:

    @staticmethod
    async def registrar_transacao(
        motorista_id: str, 
        tipo_movimentacao: str, 
        categoria: str, 
        valor: float, 
        descricao: str, 
        wpp_msg_id: str
    ) -> Dict[str, Any]:
        """
        Registra uma transação financeira atrelando-a automaticamente ao turno aberto (se houver).
        Implementa idempotência atômica via constraint do banco (Bank-Grade).
        """
        # 1. Validação de Domínio Estrita
        tipo_movimentacao = tipo_movimentacao.lower().strip()
        if tipo_movimentacao not in ('receita', 'despesa'):
            return {"status": "error", "message": "O tipo de movimentação deve ser 'receita' ou 'despesa'."}
        
        try:
            # Proteção contra falhas do IEEE 754 (Float Math)
            valor_decimal = Decimal(str(valor))
            if valor_decimal <= 0:
                return {"status": "error", "message": "O valor financeiro deve ser estritamente maior que zero."}
        except InvalidOperation:
            return {"status": "error", "message": "Valor financeiro mal formatado."}

        async with DatabaseService.get_connection() as conn:
            async with conn.transaction():
                # Isolamento estrito RLS Multi-Tenant
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true)", motorista_id)

                # Busca o turno ativo do motorista no momento da transação (pode ser NULL se fora de turno)
                turno_id = await conn.fetchval("""
                    SELECT id FROM turnos 
                    WHERE motorista_id = $1::uuid AND status = 'ABERTO' 
                    LIMIT 1
                """, motorista_id)

                # Instrução Atômica: Se o wpp_msg_id já existir, o banco rejeita silenciosamente (DO NOTHING)
                query = """
                    INSERT INTO transacoes (
                        motorista_id, turno_id, tipo_movimentacao, categoria, valor, descricao, wpp_msg_id
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
                    ON CONFLICT (wpp_msg_id) DO NOTHING
                    RETURNING id, data_transacao;
                """
                row = await conn.fetchrow(
                    query, 
                    motorista_id, 
                    turno_id, 
                    tipo_movimentacao, 
                    categoria, 
                    valor_decimal, 
                    descricao, 
                    wpp_msg_id
                )
                
                if not row:
                    logger.warning(f"Interceptada tentativa duplicada de transação (MsgID: {wpp_msg_id}).")
                    return {"status": "duplicate", "message": "Transação já registada. Ignorada por idempotência."}

                return {
                    "status": "success", 
                    "transacao_id": str(row["id"]), 
                    "turno_id": str(turno_id) if turno_id else None,
                    "data_transacao": row["data_transacao"]
                }

    @staticmethod
    async def estornar_transacao(motorista_id: str, transacao_id: str) -> Dict[str, Any]:
        """
        Aplica um Soft-Delete (estornado = TRUE) garantindo integridade de logs contábeis.
        """
        async with DatabaseService.get_connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true)", motorista_id)
                
                query = """
                    UPDATE transacoes 
                    SET estornado = TRUE 
                    WHERE id = $1::uuid 
                      AND motorista_id = $2::uuid 
                      AND estornado = FALSE
                    RETURNING id, tipo_movimentacao, valor;
                """
                row = await conn.fetchrow(query, transacao_id, motorista_id)
                
                if not row:
                    return {"status": "error", "message": "Transação não encontrada, não pertence ao motorista, ou já estornada."}
                
                return {
                    "status": "success", 
                    "message": f"{row['tipo_movimentacao'].capitalize()} de R$ {row['valor']} estornada com sucesso."
                }

    @staticmethod
    async def obter_resumo_diario(motorista_id: str, data_referencia_iso: str) -> Dict[str, Any]:
        """
        Extrai o DRE rápido do dia cruzando receitas e despesas.
        A delegação do cálculo (SUM com FILTER) é feita 100% no motor C do PostgreSQL para máxima performance.
        """
        async with DatabaseService.get_connection() as conn:
            # O isolamento também protege as consultas (Selects)
            await conn.execute("SELECT set_config('app.current_driver_id', $1, true)", motorista_id)
            
            # Ajuste 'America/Sao_Paulo' conforme a TZ da infraestrutura
            query = """
                SELECT 
                    COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0) as total_receitas,
                    COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0) as total_despesas
                FROM transacoes
                WHERE motorista_id = $1::uuid 
                  AND estornado = FALSE
                  AND DATE(data_transacao AT TIME ZONE 'America/Sao_Paulo') = $2::date
            """
            row = await conn.fetchrow(query, motorista_id, data_referencia_iso)
            
            receitas = Decimal(str(row["total_receitas"]))
            despesas = Decimal(str(row["total_despesas"]))
            saldo_liquido = receitas - despesas
            
            return {
                "status": "success",
                "data": data_referencia_iso,
                "financeiro": {
                    "receitas": float(receitas),
                    "despesas": float(despesas),
                    "saldo_liquido": float(saldo_liquido)
                }
            }

=======
from services.database_service import DatabaseService

class TransacaoService:
    @staticmethod
    async def registrar_transacao(driver_id: str, tipo: str, categoria: str, valor: float, descricao: str, hash_msg: str):
        # Utiliza o Context Manager para garantir a devolução ao Pool
        async with DatabaseService.get_connection() as conn:
            # Garante a atomicidade (Commit automático se sucesso, Rollback se exceção)
            async with conn.transaction():
                
                # Correção 2: Injeção de RLS nativa e segura para o contexto do motor de DB
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true)", driver_id)
                
                # Barreira de Idempotência
                existe = await conn.fetchval(
                    "SELECT 1 FROM transacoes WHERE driver_id = $1 AND hash_idempotencia = $2",
                    driver_id, hash_msg
                )
                if existe:
                    return {"status": "duplicate", "message": "Transação já registada anteriormente."}

                query = """
                    INSERT INTO transacoes (driver_id, tipo, categoria, valor, descricao, hash_idempotencia)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id, data_criacao;
                """
                row = await conn.fetchrow(query, driver_id, tipo, categoria, valor, descricao, hash_msg)
                return {"status": "success", "id": str(row["id"]), "data": row["data_criacao"]}
>>>>>>> b6763d0 (Atualização dos arquivos do projeto CFO Inteligente B2C)
