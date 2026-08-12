import os
import asyncpg
from datetime import datetime
from services.database_service import DatabaseService

class TurnoService:
    @staticmethod
    async def abrir_turno(motorista_id: str, veiculo_id: str, km_inicial: float) -> dict:
        """
        Abre um novo turno para o motorista, validando se já existe turno ativo.
        Utiliza isolamento RLS e garante consistência transacional.
        """
        pool = await DatabaseService.obter_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Injeta o driver_id para ativar a política de Row Level Security (RLS)
                await conn.execute("SET LOCAL app.current_driver_id = $1;", motorista_id)
                
                # Verifica se já existe um turno ativo
                turno_ativo = await conn.fetchrow(
                    """
                    SELECT id FROM turnos 
                    WHERE motorista_id = $1::uuid 
                      AND status IN ('em_andamento', 'em_pausa', 'aberto');
                    """,
                    motorista_id
                )
                
                if turno_ativo:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Já tens um turno em aberto. Encerra o turno atual antes de iniciar um novo.",
                        "tipo_erro": "TURNO_JA_ATIVO"
                    }
                
                # Insere o novo turno com status em andamento
                row = await conn.fetchrow(
                    """
                    INSERT INTO turnos (motorista_id, veiculo_id, km_inicial, status, data_inicio)
                    VALUES ($1::uuid, $2::uuid, $3, 'em_andamento', CURRENT_TIMESTAMP)
                    RETURNING id, data_inicio;
                    """,
                    motorista_id, veiculo_id, km_inicial
                )
                
                return {
                    "sucesso": True,
                    "turno_id": str(row["id"]),
                    "data_inicio": row["data_inicio"]
                }

    @staticmethod
    async def fechar_turno_com_dre(motorista_id: str, km_final: float) -> dict:
        """
        Encerra o turno ativo, valida a quilometragem percorrida e calcula o DRE executivo completo.
        """
        pool = await DatabaseService.obter_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Configura RLS
                await conn.execute("SET LOCAL app.current_driver_id = $1;", motorista_id)
                
                # Busca o turno ativo do motorista
                turno = await conn.fetchrow(
                    """
                    SELECT id, km_inicial, data_inicio, veiculo_id
                    FROM turnos
                    WHERE motorista_id = $1::uuid 
                      AND status IN ('em_andamento', 'em_pausa', 'aberto')
                    LIMIT 1;
                    """,
                    motorista_id
                )
                
                if not turno:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Nenhum turno ativo encontrado para fechar.",
                        "tipo_erro": "NENHUM_TURNO_ATIVO"
                    }
                
                km_inicial = float(turno["km_inicial"])
                
                # Validação de divergência no odômetro
                if km_final < km_inicial:
                    return {
                        "sucesso": False,
                        "erro": f"⚠️ Quilometragem final ({km_final:,.1f} km) menor que a inicial ({km_inicial:,.1f} km). Confere o painel!".replace(",", "."),
                        "tipo_erro": "ODOMETRO_DIVERGENTE"
                    }
                
                turno_id = str(turno["id"])
                data_inicio = turno["data_inicio"]
                
                # Atualiza o turno no banco para concluído
                await conn.execute(
                    """
                    UPDATE turnos
                    SET km_final = $1, status = 'concluido', data_fim = CURRENT_TIMESTAMP
                    WHERE id = $2::uuid;
                    """,
                    km_final, turno_id
                )
                
                # Recupera todas as transações da jornada (vinculadas ao turno_id ou ocorridas durante o período)
                transacoes = await conn.fetch(
                    """
                    SELECT tipo, categoria, valor, descricao, created_at
                    FROM transacoes
                    WHERE motorista_id = $1::uuid 
                      AND (turno_id = $2::uuid OR (turno_id IS NULL AND created_at >= $3));
                    """,
                    motorista_id, turno_id, data_inicio
                )
                
                faturamento_bruto = 0.0
                custo_variavel = 0.0
                despesas_detalhadas = []
                
                for t in transacoes:
                    val = float(t["valor"])
                    if t["tipo"] == "receita":
                        faturamento_bruto += val
                    elif t["tipo"] == "despesa":
                        custo_variavel += val
                        despesas_detalhadas.append({
                            "categoria": t["categoria"],
                            "descricao_original": t["descricao"],
                            "valor": val
                        })
                
                # Cálculo temporal
                agora_utc = datetime.utcnow()
                inicio_naive = data_inicio.replace(tzinfo=None) if data_inicio.tzinfo else data_inicio
                delta_tempo = agora_utc - inicio_naive
                tempo_total_min = max(delta_tempo.total_seconds() / 60.0, 1.0)
                horas_trabalhadas = tempo_total_min / 60.0
                
                km_rodados = km_final - km_inicial
                
                # Parâmetros padrão de rateio de custo fixo e metas (configuráveis futuramente por tenant)
                custo_fixo_rateado = 45.00
                meta_mensal = 6000.00
                dias_uteis = 22
                
                margem_contribuicao = faturamento_bruto - custo_variavel
                lucro_liquido_real = margem_contribuicao - custo_fixo_rateado
                
                data_inicio_str = data_inicio.strftime("%H:%M")
                data_fim_str = datetime.now().strftime("%H:%M")
                
                return {
                    "sucesso": True,
                    "data_inicio": data_inicio_str,
                    "data_fim": data_fim_str,
                    "tempo_total_min": tempo_total_min,
                    "horas_trabalhadas": horas_trabalhadas,
                    "km_inicial": km_inicial,
                    "km_final": km_final,
                    "km_rodados": km_rodados,
                    "faturamento_bruto": faturamento_bruto,
                    "custo_variavel": custo_variavel,
                    "custo_fixo_rateado": custo_fixo_rateado,
                    "lucro_liquido_real": lucro_liquido_real,
                    "meta_mensal": meta_mensal,
                    "dias_uteis": dias_uteis,
                    "despesas_detalhadas": despesas_detalhadas
                }
