import os
import asyncpg
from typing import Optional

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:strong_password_here@cfo_postgres:5432/cfo_b2c")

class DatabaseService:
    @staticmethod
    async def obter_conexao():
        return await asyncpg.connect(DATABASE_URL)

    @staticmethod
    async def buscar_motorista_por_telefone(telefone: str) -> Optional[dict]:
        """Verifica se o motorista já está cadastrado no sistema."""
        conn = await DatabaseService.obter_conexao()
        try:
            row = await conn.fetchrow(
                "SELECT id, nome, status_assinatura, ativo FROM motoristas WHERE telefone = $1",
                telefone
            )
            return dict(row) if row else None
        finally:
            await conn.close()

    @staticmethod
    async def registrar_novo_motorista(telefone: str, nome: str, veiculo_modelo: str, combustivel: str, placa: str) -> str:
        """
        Executa o onboarding transacional: cria o motorista e o seu primeiro veículo associado,
        retornando o UUID gerado do motorista.
        """
        conn = await DatabaseService.obter_conexao()
        async with conn.transaction():
            # 1. Cria o motorista
            motorista_row = await conn.fetchrow(
                """
                INSERT INTO motoristas (telefone, nome, status_assinatura)
                VALUES ($1, $2, 'TRIAL')
                RETURNING id;
                """,
                telefone, nome
            )
            motorista_id = str(motorista_row["id"])

            # 2. Configura a variável RLS para esta transação e cria o veículo inicial
            await conn.execute("SELECT set_config('app.current_driver_id', $1, true);", motorista_id)
            
            await conn.execute(
                """
                INSERT INTO veiculos (motorista_id, placa, modelo, tipo_combustivel)
                VALUES ($1::uuid, $2, $3, $4);
                """,
                motorista_id, placa.upper(), veiculo_modelo, combustivel.lower()
            )

            # 3. Cria as caixas de provisão iniciais padrão para o motorista
            await conn.execute(
                """
                INSERT INTO caixas_provisao (motorista_id, nome_caixa, saldo_atual)
                VALUES 
                ($1::uuid, 'Reserva de Manutenção', 0.00),
                ($1::uuid, 'Reserva IPVA/Seguro', 0.00);
                """,
                motorista_id
            )

        await conn.close()
        return motorista_id

    @staticmethod
    async def inserir_transacao_com_rls(motorista_id: str, tipo: str, categoria: str, valor: float, estabelecimento: str, wpp_msg_id: str):
        """Insere uma transação financeira garantindo o contexto seguro de RLS por motorista."""
        conn = await DatabaseService.obter_conexao()
        async with conn.transaction():
            # Define o contexto do motorista para a sessão de RLS
            await conn.execute("SELECT set_config('app.current_driver_id', $1, true);", motorista_id)
            
            await conn.execute(
                """
                INSERT INTO transacoes (motorista_id, tipo_movimentacao, categoria, valor, estabelecimento, idempotencia_hash)
                VALUES ($1::uuid, $2, $3, $4, $5, $6)
                ON CONFLICT (idempotencia_hash) DO NOTHING;
                """,
                motorista_id, tipo, categoria, valor, estabelecimento, wpp_msg_id
            )
        await conn.close()
