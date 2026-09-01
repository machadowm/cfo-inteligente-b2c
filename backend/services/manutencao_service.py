"""
ManutencaoService — Gestão de Manutenção Preventiva e Corretiva do Veículo Ativo.

Trabalha com as tabelas public.regras_manutencao e public.historico_manutencao.

Responsabilidades:
  1. Detecção automática de execução via matching léxico na descrição da transação.
  2. Listagem de alertas de manutenções próximas ou atrasadas (usado no DRE e perfil).
  3. Relatório completo de status de todas as regras ativas do veículo.
  4. Criação e desativação de regras preventivas baseadas em intervalo de KM.
  5. Registro manual de execuções com transação financeira correspondente.

Integração:
  • transacao_service.registrar_transacao → chama detectar_e_registrar_automatica
    após INSERT bem-sucedido de categoria 'manutencao', dentro da mesma conexão.
  • turno_service.fechar_turno_com_dre → chama obter_alertas_manutencao e inclui
    o resultado no dict de retorno para o orquestrador formatar no DRE.
  • orchestrator_service → rota !manutencao chama formatar_relatorio_manutencao.
"""

import logging
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)

# ── Mapeamento de sinônimos de oficina ───────────────────────────────────────
# Cada chave é a "âncora semântica" que deve aparecer no tipo_servico da regra.
# O matching bate quando qualquer sinônimo é encontrado como palavra inteira
# na descrição normalizada da transação.
#
# Critérios de inclusão:
#   • Grafia canônica (sem acento, minúsculas)
#   • Grafias fonéticas comuns de motoristas (ex: "oleu", "pastila", "amortecedor")
#   • Abreviações urbanas de oficina (ex: "oleo", "past", "amor")
#   • Termos de nota fiscal / orçamento de oficina
#
# NÃO incluir termos ambíguos que colidem com vocabulário cotidiano fora do contexto
# de manutenção (ex: "cabo" pode ser cabo USB; "disco" pode ser disco de música).
# Nesses casos, o motorista deve usar o comando manual !manutencao registrar.
_SINONIMOS: Dict[str, List[str]] = {
    "oleo": [
        # grafias canônicas
        "oleo", "lubrificante", "lubrificacao",
        # termos de nota fiscal / orçamento
        "troca oleo", "troca de oleo", "filtro oleo", "filtro de oleo",
        "oleo motor", "oleo cambio", "oleo diferencial",
        # grafias fonéticas/erros comuns
        "oleu", "olio", "olho motor",
        # abreviações de oficina
        "lub",
    ],
    "pneu": [
        # grafias canônicas
        "pneu", "pneus", "borracharia", "borracha",
        # serviços associados
        "alinhamento", "balanceamento", "geometria", "calibragem", "calibrar",
        # componentes
        "aro", "rodas", "estepe",
        # grafias fonéticas/erros comuns
        "pnei", "pneumatico",
        # abreviações de nota fiscal
        "balan",
    ],
    "freio": [
        # grafias canônicas
        "freio", "freios",
        # componentes
        "pastilha", "pastilhas", "disco freio", "disco de freio", "lona", "lonas",
        "fluido freio", "fluido de freio", "fluido",
        # sistemas
        "abs",
        # grafias fonéticas/erros comuns de motoristas
        "pastila", "pastillas", "pastiha", "freo", "freeo",
        # abreviações de nota fiscal
        "past",
    ],
    "suspensao": [
        # grafias canônicas
        "suspensao", "suspenção",
        # componentes
        "amortecedor", "amortecedores", "mola", "molas", "pivo", "pivô",
        "batente", "coifa", "bandeja", "bandejas", "cubo", "rolamento",
        # grafias fonéticas/erros comuns
        "amortecedô", "amorteçedor", "amor", "suspençao",
    ],
    "correia": [
        # grafias canônicas
        "correia", "correias",
        # tipos
        "correia dentada", "correia do alternador", "correia acessorios",
        # componentes do kit
        "tensor", "tensores", "polia", "polias", "bomba dagua", "bomba de agua",
        # grafias fonéticas/erros comuns
        "coria", "corea",
        # abreviações de nota fiscal
        "kit correia", "kit distribuicao",
    ],
    "vela": [
        # grafias canônicas
        "vela", "velas",
        # componentes do sistema de ignição
        "ignicao", "cabo vela", "cabo de vela", "cabos", "bobina", "bobinas",
        # grafias fonéticas/erros comuns
        "vella", "velas ignicao",
    ],
    "ar": [
        # grafias canônicas sem espaço (após normalização remove pontuação mas mantém espaço)
        "ar condicionado", "arcondicionado",
        # serviços
        "carga gas", "cargagas", "higienizacao ar", "higienizacao",
        # componentes
        "filtro cabine", "filtrocabine", "compressor ar",
        # grafias fonéticas/erros comuns
        "ar frio", "ar quente", "gelando", "refrigeracao",
    ],
    "mecanico": [
        # grafias canônicas
        "mecanico", "mecanica", "oficina",
        # tipos de serviço
        "revisao", "revisão", "diagnostico", "diagnóstico", "reparo", "reparos",
        # contexto de nota fiscal
        "mao de obra", "mao obra", "servico mecanico",
        # grafias fonéticas/erros comuns
        "mecanicu", "mekanico", "oficinia",
    ],
    "embreagem": [
        # grafias canônicas
        "embreagem",
        # componentes do kit
        "disco embreagem", "platô", "plato", "rolamento embreagem",
        # grafias fonéticas/erros comuns
        "imbreagem", "embraiagem", "embragem",
    ],
    "bateria": [
        # grafias canônicas
        "bateria", "baterias",
        # contexto elétrico
        "bateria carro", "carga bateria",
        # grafias fonéticas/erros comuns
        "batéria", "batira",
    ],
    "injecao": [
        # grafias canônicas
        "injecao", "injeção",
        # componentes
        "bicos injetores", "bico injetor", "limpeza injecao", "limpeza bicos",
        # grafias fonéticas/erros comuns
        "injeção eletronica", "injecao eletronica",
    ],
}


def _normalizar(texto: str) -> str:
    """Remove acentos, pontuação e converte para minúsculas."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9\s]", "", sem_acento).lower().strip()


def _palavras_chave_para_tipo(tipo_servico: str) -> List[str]:
    """
    Retorna lista de palavras-chave de matching para um tipo_servico normalizado.
    Inclui o próprio tipo normalizado mais todos os sinônimos cuja âncora aparece
    como substring do tipo.
    """
    tipo_norm = _normalizar(tipo_servico)
    resultado = {tipo_norm}
    for ancora, sinonimos in _SINONIMOS.items():
        if ancora in tipo_norm:
            resultado.update(sinonimos)
    return list(resultado)


def _contem_palavra(desc: str, palavra: str) -> bool:
    """
    Verifica se `palavra` aparece em `desc` sem ser substring de outra palavra.

    • Token simples (sem espaço): usa \\b para evitar falsos positivos de substring
      (ex: 'oleo' não bate em 'olhei').
    • Frase com espaços (ex: 'troca oleo'): usa lookaround de word boundary nas
      extremidades, permitindo que o espaço interno bata normalmente.
      Isso garante que 'troca oleo' bate em 'fiz troca oleo hoje' mas não em
      'trocaoleo' (sem espaço, seria grafia inválida de qualquer forma).
    """
    palavra_norm = _normalizar(palavra)
    if not palavra_norm:
        return False
    pattern = r"\b" + re.escape(palavra_norm) + r"\b"
    return bool(re.search(pattern, desc))


class ManutencaoService:

    # ── Regras ───────────────────────────────────────────────────────────────

    @staticmethod
    async def criar_regra(
        motorista_id: str,
        tipo_servico: str,
        intervalo_km: int,
        aviso_previo_km: int = 500,
    ) -> Dict[str, Any]:
        """Cria uma regra preventiva para o veículo ativo do motorista."""
        if intervalo_km <= 0:
            return {"sucesso": False, "erro": "O intervalo de km deve ser maior que zero."}
        if aviso_previo_km < 0:
            return {"sucesso": False, "erro": "O aviso prévio não pode ser negativo."}
        if aviso_previo_km >= intervalo_km:
            return {"sucesso": False, "erro": "O aviso prévio deve ser menor que o intervalo."}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                veiculo = await conn.fetchrow(
                    "SELECT id, modelo, placa FROM public.veiculos "
                    "WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY created_at DESC LIMIT 1;",
                    motorista_id,
                )
                if not veiculo:
                    return {"sucesso": False, "erro": "Nenhum veículo ativo localizado."}

                regra_id = await conn.fetchval(
                    """
                    INSERT INTO public.regras_manutencao
                        (veiculo_id, tipo_servico, intervalo_km, aviso_previo_km, ativo)
                    VALUES ($1::uuid, $2, $3, $4, TRUE)
                    RETURNING id;
                    """,
                    veiculo["id"], tipo_servico.strip(), intervalo_km, aviso_previo_km,
                )
                return {
                    "sucesso": True,
                    "regra_id": str(regra_id),
                    "veiculo_modelo": veiculo["modelo"],
                    "veiculo_placa": veiculo["placa"],
                }
        except Exception as e:
            logger.error(f"[ManutencaoService] criar_regra: {e}")
            return {"sucesso": False, "erro": f"Erro interno: {e}"}

    @staticmethod
    async def remover_regra(motorista_id: str, regra_id: str) -> Dict[str, Any]:
        """Desativa logicamente uma regra (soft-delete)."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                result = await conn.execute(
                    """
                    UPDATE public.regras_manutencao rm
                    SET ativo = FALSE
                    FROM public.veiculos v
                    WHERE rm.veiculo_id = v.id
                      AND v.motorista_id = $1::uuid
                      AND rm.id = $2::uuid;
                    """,
                    motorista_id, regra_id,
                )
                n = int(result.split()[-1]) if result else 0
                if n == 0:
                    return {"sucesso": False, "erro": "Regra não encontrada ou não pertence ao seu veículo."}
                return {"sucesso": True}
        except Exception as e:
            logger.error(f"[ManutencaoService] remover_regra: {e}")
            return {"sucesso": False, "erro": f"Erro interno: {e}"}

    # ── Histórico manual ─────────────────────────────────────────────────────

    @staticmethod
    async def registrar_manual(
        motorista_id: str,
        tipo_servico: str,
        km_execucao: float,
        custo: float,
        regra_id: Optional[str] = None,
        data_execucao: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Registra uma execução manualmente: cria a transação de despesa
        e o vínculo em historico_manutencao atomicamente.
        """
        try:
            km_dec = Decimal(str(km_execucao)).quantize(Decimal("0.01"), ROUND_HALF_UP)
            custo_dec = Decimal(str(custo)).quantize(Decimal("0.01"), ROUND_HALF_UP)
            dt_exec = data_execucao or datetime.now()

            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                veiculo = await conn.fetchrow(
                    "SELECT id FROM public.veiculos "
                    "WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY created_at DESC LIMIT 1;",
                    motorista_id,
                )
                if not veiculo:
                    return {"sucesso": False, "erro": "Nenhum veículo ativo localizado."}

                veiculo_id = veiculo["id"]

                if regra_id:
                    ok = await conn.fetchval(
                        "SELECT id FROM public.regras_manutencao "
                        "WHERE id = $1::uuid AND veiculo_id = $2::uuid AND ativo = TRUE;",
                        regra_id, veiculo_id,
                    )
                    if not ok:
                        return {"sucesso": False, "erro": "Regra inválida para o veículo atual."}

                wpp_id = f"manual_maint_{int(dt_exec.timestamp())}_{regra_id or 'geral'}"
                tx_id = await conn.fetchval(
                    """
                    INSERT INTO public.transacoes (
                        motorista_id, veiculo_id, tipo_movimentacao, categoria,
                        valor, descricao, wpp_msg_id, odometro_abastecimento, data_transacao
                    ) VALUES ($1::uuid, $2::uuid, 'despesa', 'manutencao',
                              $3, $4, $5, $6, $7)
                    RETURNING id;
                    """,
                    motorista_id, veiculo_id, custo_dec,
                    f"Manutenção: {tipo_servico}", wpp_id, km_dec, dt_exec,
                )

                # regra_id pode ser NULL — asyncpg aceita None diretamente como uuid nullable
                hist_id = await conn.fetchval(
                    """
                    INSERT INTO public.historico_manutencao
                        (veiculo_id, regra_id, transacao_id, km_execucao, data_execucao)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5)
                    RETURNING id;
                    """,
                    veiculo_id, regra_id, tx_id, km_dec, dt_exec,
                )
                return {
                    "sucesso": True,
                    "historico_id": str(hist_id),
                    "transacao_id": str(tx_id),
                    "valor_registrado": float(custo_dec),
                }
        except Exception as e:
            logger.error(f"[ManutencaoService] registrar_manual: {e}")
            return {"sucesso": False, "erro": f"Erro interno: {e}"}

    # ── Detecção automática (trigger lógico) ─────────────────────────────────

    @staticmethod
    async def detectar_e_registrar_automatica(
        conn,
        motorista_id: str,
        transacao_id: str,
        descricao: str,
        km_execucao: Decimal,
    ) -> Optional[Dict[str, Any]]:
        """
        Chamado dentro da mesma conexão/transação do INSERT de transacao de
        categoria 'manutencao'. Faz matching léxico da descrição contra as regras
        ativas e insere em historico_manutencao se encontrar correspondência.

        Falha silenciosa: qualquer erro é logado mas não propaga, para não
        bloquear o fluxo contábil principal.
        """
        try:
            desc_norm = _normalizar(descricao)

            regras = await conn.fetch(
                """
                SELECT rm.id, rm.tipo_servico, v.id AS veiculo_id
                FROM public.regras_manutencao rm
                JOIN public.veiculos v ON v.id = rm.veiculo_id
                WHERE v.motorista_id = $1::uuid AND v.ativo = TRUE AND rm.ativo = TRUE;
                """,
                motorista_id,
            )
            if not regras:
                return None

            regra_match = None
            veiculo_id_match = None
            for r in regras:
                kws = _palavras_chave_para_tipo(r["tipo_servico"])
                if any(_contem_palavra(desc_norm, kw) for kw in kws):
                    regra_match = r
                    veiculo_id_match = r["veiculo_id"]
                    break

            if not regra_match:
                return None

            # Guarda km_execucao = 0 quando não disponível (fallback; odômetro pode ser
            # atualizado posteriormente via registrar_manual se necessário).
            km_val = km_execucao if km_execucao > Decimal("0") else Decimal("0.00")

            hist_id = await conn.fetchval(
                """
                INSERT INTO public.historico_manutencao
                    (veiculo_id, regra_id, transacao_id, km_execucao)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
                RETURNING id;
                """,
                veiculo_id_match, regra_match["id"], transacao_id, km_val,
            )
            logger.info(
                f"[ManutencaoService] Auto-link: tx={transacao_id} → "
                f"regra='{regra_match['tipo_servico']}' hist={hist_id}"
            )
            return {
                "regra_id": str(regra_match["id"]),
                "tipo_servico": regra_match["tipo_servico"],
                "historico_id": str(hist_id),
            }
        except Exception as e:
            logger.error(f"[ManutencaoService] detectar_e_registrar_automatica: {e}")
            return None

    # ── Alertas (usado no DRE e perfil) ──────────────────────────────────────

    @staticmethod
    async def obter_alertas(
        conn,
        motorista_id: str,
        km_atual: Decimal,
    ) -> List[Dict[str, Any]]:
        """
        Retorna lista de regras em estado ATRASADA ou ALERTA.
        Roda dentro de uma conexão já aberta (mesma transação do fechamento).
        Retorna [] em caso de erro para não bloquear o DRE.
        """
        try:
            regras = await conn.fetch(
                """
                SELECT rm.id, rm.tipo_servico, rm.intervalo_km, rm.aviso_previo_km,
                       v.id AS veiculo_id
                FROM public.regras_manutencao rm
                JOIN public.veiculos v ON v.id = rm.veiculo_id
                WHERE v.motorista_id = $1::uuid AND v.ativo = TRUE AND rm.ativo = TRUE
                ORDER BY rm.intervalo_km ASC;
                """,
                motorista_id,
            )
            alertas = []
            for r in regras:
                ultimo = await conn.fetchrow(
                    """
                    SELECT km_execucao, data_execucao
                    FROM public.historico_manutencao
                    WHERE veiculo_id = $1::uuid AND regra_id = $2::uuid
                    ORDER BY km_execucao DESC, data_execucao DESC
                    LIMIT 1;
                    """,
                    r["veiculo_id"], r["id"],
                )
                km_ultima  = Decimal(str(ultimo["km_execucao"])) if ultimo else Decimal("0.00")
                data_ultima = ultimo["data_execucao"] if ultimo else None
                intervalo  = Decimal(str(r["intervalo_km"]))
                aviso      = Decimal(str(r["aviso_previo_km"]))
                km_proxima = km_ultima + intervalo
                km_restante = km_proxima - km_atual

                if km_restante <= Decimal("0"):
                    status = "ATRASADA"
                elif km_restante <= aviso:
                    status = "ALERTA"
                else:
                    continue  # OK — não inclui na lista de alertas

                alertas.append({
                    "regra_id": str(r["id"]),
                    "tipo_servico": r["tipo_servico"],
                    "intervalo_km": int(intervalo),
                    "km_ultima_execucao": float(km_ultima),
                    "data_ultima_execucao": data_ultima.strftime("%d/%m/%Y") if data_ultima else "Nunca",
                    "km_proxima_execucao": float(km_proxima),
                    "km_restantes": float(km_restante),
                    "status": status,
                })
            return alertas
        except Exception as e:
            logger.error(f"[ManutencaoService] obter_alertas: {e}")
            return []

    # ── Relatório completo (comando !manutencao) ──────────────────────────────

    @staticmethod
    async def formatar_relatorio(motorista_id: str, km_atual: float) -> str:
        """Gera o painel completo de status de todas as regras ativas."""
        try:
            km_dec = Decimal(str(km_atual))
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                veiculo = await conn.fetchrow(
                    "SELECT modelo, placa FROM public.veiculos "
                    "WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY created_at DESC LIMIT 1;",
                    motorista_id,
                )
                if not veiculo:
                    return "⚠ Nenhum veículo ativo localizado."

                regras = await conn.fetch(
                    """
                    SELECT rm.id, rm.tipo_servico, rm.intervalo_km, rm.aviso_previo_km,
                           (SELECT MAX(km_execucao)
                            FROM public.historico_manutencao
                            WHERE regra_id = rm.id) AS km_ultima
                    FROM public.regras_manutencao rm
                    WHERE rm.veiculo_id = (
                        SELECT id FROM public.veiculos
                        WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY created_at DESC LIMIT 1
                    ) AND rm.ativo = TRUE
                    ORDER BY rm.intervalo_km ASC;
                    """,
                    motorista_id,
                )

                if not regras:
                    return (
                        f"🔧  *Controle de Manutenção*  🛡\n"
                        f"Veículo: *{veiculo['modelo']}*  ({veiculo['placa']})\n\n"
                        "Nenhuma regra cadastrada ainda.\n\n"
                        "Para criar, envie:\n"
                        "👉  *!manutencao criar troca_oleo 10000*\n"
                        "_Ex: nome do serviço + intervalo em km (aviso padrão: 500 km antes)_"
                    )

                linhas = [
                    "🔧  *MANUTENÇÃO PREVENTIVA*  🛡",
                    f"Veículo: *{veiculo['modelo']}*  ({veiculo['placa']})",
                    f"Odômetro atual: *{km_atual:,.1f} km*\n".replace(",", "."),
                ]
                for r in regras:
                    km_ult = Decimal(str(r["km_ultima"])) if r["km_ultima"] is not None else Decimal("0.00")
                    intervalo = Decimal(str(r["intervalo_km"]))
                    aviso     = Decimal(str(r["aviso_previo_km"]))
                    km_prox   = km_ult + intervalo
                    km_rest   = km_prox - km_dec

                    if km_rest <= Decimal("0"):
                        badge = "🔴 ATRASADA"
                        obs   = f"Atrasou há  *{abs(int(km_rest)):,} km*!".replace(",", ".")
                    elif km_rest <= aviso:
                        badge = "🟡 ALERTA"
                        obs   = f"Faltam  *{int(km_rest):,} km*  para fazer.".replace(",", ".")
                    else:
                        badge = "🟢 OK"
                        obs   = f"Faltam  *{int(km_rest):,} km*.".replace(",", ".")

                    linhas.append(
                        f"• *{r['tipo_servico']}*  ({badge})\n"
                        f"  ├ Intervalo: {int(intervalo):,} km\n".replace(",", ".") +
                        f"  ├ Última: {int(km_ult):,} km\n".replace(",", ".") +
                        f"  └ {obs}"
                    )

                linhas.append(
                    "\n💡 _Ao lançar um gasto de manutenção (ex:  *gastei 180 troca oleo 182000* ), "
                    "vinculo automaticamente ao histórico do veículo!_"
                )
                return "\n".join(linhas)
        except Exception as e:
            logger.error(f"[ManutencaoService] formatar_relatorio: {e}")
            return "❌ Erro ao gerar o painel de manutenção."


def formatar_alertas_manutencao(alertas: List[Dict[str, Any]]) -> str:
    """
    Formata a lista de alertas (ATRASADA/ALERTA) para inserção inline
    no DRE de fechamento ou no perfil.
    Retorna string vazia se não houver alertas.
    """
    if not alertas:
        return ""
    linhas = ["🔧  *MANUTENÇÃO — ATENÇÃO NECESSÁRIA*"]
    for a in alertas:
        if a["status"] == "ATRASADA":
            km_atraso = abs(int(a["km_restantes"]))
            linhas.append(
                f"🔴  *{a['tipo_servico']}*  — Atrasada há  *{km_atraso:,} km*  "
                f"(última: {a['data_ultima_execucao']})".replace(",", ".")
            )
        else:
            linhas.append(
                f"🟡  *{a['tipo_servico']}*  — Faltam  *{int(a['km_restantes']):,} km*  "
                f"(prevista: {int(a['km_proxima_execucao']):,} km)".replace(",", ".")
            )
    linhas.append("_Envie  *!manutencao*  para o painel completo._\n")
    return "\n".join(linhas) + "\n"
