import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:strong_password_here@cfo_postgres:5432/cfo_b2c")

class TransacaoService:
    @staticmethod
    async def registrar_transacao(motorista_id: str, tipo_movimentacao: str, categoria: str, valor: float, descricao_original: str, wpp_msg_id: str) -> dict:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            async with conn.transaction():
                # Define a diretiva RLS para o driver atual na sessão da transação
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true);", motorista_id)

                # Procura se existe um turno em andamento ou pausado para associar a transação ao turno
                turno_ativo = await conn.fetchrow(
                    """
                    SELECT id FROM turnos 
                    WHERE motorista_id = $1::uuid AND status IN ('em_andamento', 'em_pausa')
                    ORDER BY data_inicio DESC LIMIT 1;
                    """,
                    motorista_id
                )
                
                turno_id = str(turno_ativo["id"]) if turno_ativo else None

                # Inserção da transação com precisão financeira no PostgreSQL
                row = await conn.fetchrow(
                    """
                    INSERT INTO transacoes (motorista_id, turno_id, tipo_movimentacao, categoria, valor, descricao, evolution_msg_id)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
                    RETURNING id, created_at;
                    """,
                    motorista_id, turno_id, tipo_movimentacao, categoria, valor, descricao_original, wpp_msg_id
                )

                return {
                    "sucesso": True,
                    "transacao_id": str(row["id"]),
                    "turno_associado": turno_id is not None
                }
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}
        finally:
            await conn.close()
