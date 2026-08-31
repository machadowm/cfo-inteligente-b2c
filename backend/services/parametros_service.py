"""
ParametrosService — Interceptor de Comandos Administrativos via prefixo '!'.

Permite que motoristas ajustem parâmetros financeiros e contratuais críticos
sem percorrer fluxos de menus.  Toda alteração é persistida com isolamento de
tenant (RLS) e registrada em log de auditoria no Redis.

Formato do comando:
    !alterar <parâmetro> <valor>
    Exemplos:
        !alterar meta mensal 12000
        !alterar aluguel 1020,85
        !alterar dias uteis 26
        !alterar km excedente 0,75       ← preço do km além da franquia
        !alterar tanque 50               ← capacidade do tanque (litros)
        !alterar bateria 40              ← capacidade da bateria (kWh)
        !alterar km gasolina 12.5
        !alterar km etanol 9.0
        !alterar km kwh 6.5
        !alterar km m3 14.0

Gestão de despesas fixas mensais:
    !despesas fixas                      ← lista as despesas cadastradas
    !adicionar despesa <nome> <valor mensal> <dias>
        Ex: !adicionar despesa internet 120 26
    !remover despesa <nome>
        Ex: !remover despesa internet

Para listar os parâmetros disponíveis:
    !parametros
"""

import json as _json
import re
import time
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whitelist de parâmetros permitidos
# ---------------------------------------------------------------------------
# Formato: "alias amigável": (coluna_banco, tipo_python, tabela, label_exibição)
#
# Segurança: o nome da coluna NUNCA vem do usuário — é sempre resolvido a
# partir desta whitelist.  Injeção de SQL via alias é impossível.
# ---------------------------------------------------------------------------

_ParamEntry = tuple  # (coluna: str, tipo: type, tabela: str, label: str)

PARAM_MAP: dict[str, _ParamEntry] = {
    # ── Motoristas ──────────────────────────────────────────────────────────
    "meta mensal":   ("meta_mensal_faturamento",  Decimal, "motoristas", "Meta Mensal (R$)"),
    "meta":          ("meta_mensal_faturamento",  Decimal, "motoristas", "Meta Mensal (R$)"),
    "dias uteis":    ("dias_uteis_mes",            int,     "motoristas", "Dias Úteis/Mês"),
    # ── Veículos (colunas planas) ────────────────────────────────────────────
    "aluguel":       ("custo_aluguel_semanal",     Decimal, "veiculos",   "Aluguel Semanal (R$)"),
    "franquia":      ("franquia_km_semanal",       Decimal, "veiculos",   "Franquia KM Semanal (km)"),
    "km excedente":  ("valor_km_excedente",        Decimal, "veiculos",   "Preço KM Excedente (R$/km)"),
    "excedente":     ("valor_km_excedente",        Decimal, "veiculos",   "Preço KM Excedente (R$/km)"),
    # ── Capacidade física do veículo (JSONB estoque_financeiro.meta) ─────────
    # tabela prefixada com "jsonb_estoque:meta" — mesmo handler de rendimento
    "tanque":        ("capacidade_tanque_l",   Decimal, "jsonb_estoque:meta", "Capacidade do Tanque (L)"),
    "capacidade tanque": ("capacidade_tanque_l", Decimal, "jsonb_estoque:meta", "Capacidade do Tanque (L)"),
    "bateria":       ("capacidade_bateria_kwh", Decimal, "jsonb_estoque:meta", "Capacidade da Bateria (kWh)"),
    "capacidade bateria": ("capacidade_bateria_kwh", Decimal, "jsonb_estoque:meta", "Capacidade da Bateria (kWh)"),
    # ── Rendimento energético (sub-chaves JSONB em estoque_financeiro) ────────
    "km gasolina":   ("km_l_gasolina", Decimal, "jsonb_estoque:liquido",      "Rendimento Gasolina (km/L)"),
    "km/l gasolina": ("km_l_gasolina", Decimal, "jsonb_estoque:liquido",      "Rendimento Gasolina (km/L)"),
    "km etanol":     ("km_l_etanol",   Decimal, "jsonb_estoque:liquido",      "Rendimento Etanol (km/L)"),
    "km/l etanol":   ("km_l_etanol",   Decimal, "jsonb_estoque:liquido",      "Rendimento Etanol (km/L)"),
    "km kwh":        ("km_kwh",        Decimal, "jsonb_estoque:eletricidade", "Rendimento Elétrico (km/kWh)"),
    "km/kwh":        ("km_kwh",        Decimal, "jsonb_estoque:eletricidade", "Rendimento Elétrico (km/kWh)"),
    "km m3":         ("km_m3",         Decimal, "jsonb_estoque:gnv",          "Rendimento GNV (km/m³)"),
    "km/m3":         ("km_m3",         Decimal, "jsonb_estoque:gnv",          "Rendimento GNV (km/m³)"),
}

# Nomes de exibição das tabelas para a mensagem de confirmação
_TABELA_LABEL = {
    "motoristas":                 "perfil do motorista",
    "veiculos":                   "veículo ativo",
    "jsonb_estoque:meta":         "configurações do veículo",
    "jsonb_estoque:liquido":      "cofre de combustível líquido",
    "jsonb_estoque:eletricidade": "cofre elétrico",
    "jsonb_estoque:gnv":          "cofre de GNV",
}

# Alias genérico "km/l" ou "km l" sem especificação de combustível —
# resolvido dinamicamente pelo tipo_combustivel do veículo ativo.
_ALIASES_KM_GENERICOS = {"km/l", "km l", "kml"}


class ParametrosService:
    """Processa comandos administrativos iniciados com '!'."""

    # ------------------------------------------------------------------
    # Regex principal
    # Aceita:  !alterar <parâmetro com espaços> <número>
    # O valor aceita ponto ou vírgula como separador decimal.
    # ------------------------------------------------------------------
    _RE_ALTERAR = re.compile(
        r"^!alterar\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)$",
        re.IGNORECASE,
    )

    @staticmethod
    def _converter_valor(raw: str, tipo: type):
        """Converte a string capturada pelo regex para o tipo esperado pela coluna."""
        normalizado = raw.replace(",", ".")
        if tipo is Decimal:
            return Decimal(normalizado)
        if tipo is int:
            # Rejeita frações explicitamente — ex: "26,5" não é um inteiro válido
            if "." in normalizado:
                raise ValueError(f"Valor '{raw}' não é um número inteiro válido.")
            return int(normalizado)
        raise TypeError(f"Tipo {tipo} não suportado pelo conversor.")

    @staticmethod
    async def _registrar_auditoria(tenant_id: str, motorista_id: str, param: str, coluna: str, valor) -> None:
        """Grava no Redis um log de auditoria da alteração de parâmetro (TTL 30 dias)."""
        try:
            client = await RedisFSMService.get_client()
            key = f"audit_param:{tenant_id}"
            entry = f"{int(time.time())}|{param}|{coluna}|{valor}"
            async with client.pipeline(transaction=True) as pipe:
                pipe.lpush(key, entry)          # mais recente no topo
                pipe.ltrim(key, 0, 49)          # mantém apenas as 50 últimas alterações
                pipe.expire(key, 2592000)       # TTL 30 dias
                await pipe.execute()
        except Exception as exc:
            logger.warning(f"[ParametrosService] Falha ao gravar auditoria Redis (tenant={tenant_id}): {exc}")

    # ------------------------------------------------------------------
    # Regex para o comando de ajuste de estoque
    # Aceita: !ajustar estoque <fonte> <valor>
    # Exemplos:
    #   !ajustar estoque litros 35
    #   !ajustar estoque kwh 20,5
    #   !ajustar estoque m3 8
    # ------------------------------------------------------------------
    _RE_ESTOQUE = re.compile(
        r"^!ajustar\s+estoque\s+(litros|kwh|m3)\s+([\d]+(?:[.,][\d]+)?)$",
        re.IGNORECASE,
    )

    # Mapeamento de fonte → (chave_no_json_liquido/ele/gnv, subdict)
    _ESTOQUE_FONTE_MAP = {
        "litros": ("litros", "liquido"),
        "kwh":    ("kwh",    "eletricidade"),
        "m3":     ("m3",     "gnv"),
    }

    @staticmethod
    async def _ajustar_estoque(motorista_id: str, tenant_id: str, fonte: str, novo_valor: Decimal) -> str:
        """Sobrescreve a quantidade de uma fonte de energia no estoque virtual do veículo.

        Não altera o custo_total (CMP preservado) — apenas a quantidade física é corrigida.
        Isso permite que o motorista desfaça queimas acidentais por erros de odômetro sem
        alterar o histórico financeiro do cofre.
        """
        import json as _json
        chave, subdict = ParametrosService._ESTOQUE_FONTE_MAP[fonte]

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, estoque_financeiro FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY created_at DESC LIMIT 1;",
                    motorista_id,
                )
                if not row:
                    return "⚠ Nenhum veículo ativo localizado para ajuste de estoque."

                veiculo_id = str(row["id"])
                raw = row["estoque_financeiro"]
                estoque: dict = _json.loads(raw) if isinstance(raw, str) else (raw or {})

                # Garante estrutura mínima
                if subdict not in estoque:
                    from services.turno_service import TurnoService
                    estoque = TurnoService._garantir_estrutura_estoque(estoque)

                estoque[subdict][chave] = float(novo_valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                await conn.execute(
                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                    _json.dumps(estoque), veiculo_id,
                )

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id,
                f"ajustar_estoque_{fonte}", f"estoque.{subdict}.{chave}", novo_valor
            )
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")

            unidade = {"litros": "L", "kwh": "kWh", "m3": "m³"}[fonte]
            valor_fmt = f"{float(novo_valor):.2f}".replace(".", ",")
            return (
                f"✅  *Estoque ajustado com sucesso!*\n"
                f"• {fonte.upper()}:  *{valor_fmt} {unidade}*  registrado no cofre.\n"
                f"_O CMP (custo médio por unidade) foi preservado. Apenas a quantidade física foi corrigida._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao ajustar estoque (motorista={motorista_id}): {exc}")
            return "❌ Erro interno ao ajustar o estoque. Verifique o valor e tente novamente."

    @staticmethod
    async def processar(motorista_id: str, tenant_id: str, texto_bruto: str) -> Optional[str]:
        """
        Ponto de entrada principal.

        Retorna:
            str  — mensagem de resposta para o motorista (sucesso ou erro).
            None — a mensagem não é um comando '!', deve seguir fluxo normal.
        """
        texto = texto_bruto.strip()

        # ── Ajuste manual de estoque ─────────────────────────────────────
        match_est = ParametrosService._RE_ESTOQUE.match(texto)
        if match_est:
            fonte = match_est.group(1).lower()
            try:
                novo_valor = Decimal(match_est.group(2).replace(",", "."))
                if novo_valor < 0:
                    raise ValueError
            except (Exception,):
                return "⚠ Valor inválido. Use um número positivo (ex: `!ajustar estoque litros 35`)."
            return await ParametrosService._ajustar_estoque(motorista_id, tenant_id, fonte, novo_valor)

        # ── Listagem de parâmetros disponíveis ──────────────────────────
        if re.match(r"^!parametros?\b", texto, re.IGNORECASE):
            linhas = [
                "📋  *Parâmetros ajustáveis — Parceiro do Painel*\n",
                "💰  *Financeiro e Metas:*",
                "  •  *!alterar meta mensal <R$>*   →  Meta de faturamento mensal",
                "  •  *!alterar dias uteis <n>*      →  Dias úteis trabalhados por mês",
                "",
                "🚗  *Contrato e Veículo:*",
                "  •  *!alterar aluguel <R$/sem>*    →  Custo semanal do aluguel/contrato",
                "  •  *!alterar franquia <km/sem>*   →  Franquia de KM semanal",
                "  •  *!alterar km excedente <R$/km>* → Preço do KM além da franquia",
                "  •  *!alterar tanque <litros>*     →  Capacidade do tanque (afeta self-heal)",
                "  •  *!alterar bateria <kWh>*       →  Capacidade da bateria elétrica",
                "",
                "⛽  *Rendimento Energético (km/L ou km/kWh):*",
                "  •  *!alterar km gasolina <valor>* →  Rendimento gasolina (km/L)",
                "  •  *!alterar km etanol <valor>*   →  Rendimento etanol (km/L)",
                "  •  *!alterar km kwh <valor>*      →  Rendimento elétrico (km/kWh)",
                "  •  *!alterar km m3 <valor>*       →  Rendimento GNV (km/m³)",
                "",
                "🔧  *Estoque Virtual (corrigir saldo físico):*",
                "  •  *!ajustar estoque litros <qtd>* → Corrigir litros no cofre",
                "  •  *!ajustar estoque kwh <qtd>*    → Corrigir kWh no cofre",
                "  •  *!ajustar estoque m3 <qtd>*     → Corrigir m³ de GNV no cofre",
                "",
                "📌  *Despesas Fixas Mensais (seguro, internet, manutenção...):*",
                "  •  *!despesas fixas*               → Listar despesas cadastradas",
                "  •  *!adicionar despesa <nome> <R$/mês> <dias>*",
                "     _Ex: !adicionar despesa seguro 180 26_",
                "  •  *!remover despesa <nome>*        → Desativar despesa pelo nome",
                "",
                "📦  *Caixas de Provisão (reserva para despesas futuras):*",
                "  •  *!caixas*                        → Ver saldos de todas as caixas",
                "  •  *!criar caixa <nome>*             → Criar caixa avulsa",
                "  •  *!retirar caixa <nome> <valor>*   → Sacar da caixinha",
                "     _Ex: !retirar caixa seguro 180_",
                "",
                "_Exemplos:_",
                "  *!alterar meta mensal 12000*",
                "  *!alterar tanque 50*",
                "  *!alterar km excedente 0,75*",
                "  *!adicionar despesa internet 120 26*",
                "  *!caixas*",
                "  *!retirar caixa internet 120*",
            ]
            return "\n".join(linhas)

        # ── Gestão de despesas fixas mensais ─────────────────────────────
        if re.match(r"^!despesas?\s+fix", texto, re.IGNORECASE):
            return await ParametrosService._listar_despesas_fixas(motorista_id)

        match_add = re.match(
            r"^!adicionar\s+despesa\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)\s+(\d+)$",
            texto, re.IGNORECASE,
        )
        if match_add:
            nome_desp  = match_add.group(1).strip()[:50]
            valor_desp = Decimal(match_add.group(2).replace(",", "."))
            dias_desp  = int(match_add.group(3))
            return await ParametrosService._adicionar_despesa_fixa(
                motorista_id, tenant_id, nome_desp, valor_desp, dias_desp
            )

        match_rem = re.match(r"^!remover\s+despesa\s+(.+)$", texto, re.IGNORECASE)
        if match_rem:
            nome_desp = match_rem.group(1).strip()[:50]
            return await ParametrosService._remover_despesa_fixa(motorista_id, tenant_id, nome_desp)

        # ── Gestão de caixas de provisão ──────────────────────────────────
        if re.match(r"^!caixas?\b", texto, re.IGNORECASE):
            return await ParametrosService._listar_caixas(motorista_id)

        match_criar = re.match(r"^!criar\s+caixa\s+(.+)$", texto, re.IGNORECASE)
        if match_criar:
            nome_cx = match_criar.group(1).strip()[:60]
            return await ParametrosService._criar_caixa(motorista_id, tenant_id, nome_cx)

        match_retirar = re.match(
            r"^!retirar\s+caixa\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)$",
            texto, re.IGNORECASE,
        )
        if match_retirar:
            nome_cx = match_retirar.group(1).strip()[:60]
            try:
                valor_cx = Decimal(match_retirar.group(2).replace(",", "."))
            except Exception:
                return "⚠ Valor inválido. Ex:  *!retirar caixa seguro 180*"
            return await ParametrosService._retirar_caixa(motorista_id, tenant_id, nome_cx, valor_cx)

        # ── Comando !alterar ─────────────────────────────────────────────
        match = ParametrosService._RE_ALTERAR.match(texto)
        if not match:
            # Não é um comando '!' reconhecido — devolve None para o orquestrador
            return None

        param_nome = match.group(1).strip().lower()
        valor_raw  = match.group(2)

        # ── Resolução de alias genérico "km/l" / "km l" ─────────────────
        # Sem especificação de combustível, consulta o tipo do veículo ativo
        # para mapear automaticamente ao parâmetro correto.
        # Veículos Flex/Híbridos são rejeitados com instrução educativa.
        if param_nome in _ALIASES_KM_GENERICOS:
            try:
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    veiculo_row = await conn.fetchrow(
                        "SELECT tipo_combustivel, is_flex, is_hibrido "
                        "FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE "
                        "ORDER BY created_at DESC LIMIT 1;",
                        motorista_id,
                    )
                if not veiculo_row:
                    return "⚠ Nenhum veículo ativo localizado. Cadastre um veículo primeiro."
                tipo_comb = (veiculo_row["tipo_combustivel"] or "").lower()
                is_flex    = bool(veiculo_row["is_flex"])
                is_hibrido = bool(veiculo_row["is_hibrido"])
                if is_flex or is_hibrido or "flex" in tipo_comb or "hibrido" in tipo_comb:
                    return (
                        "⚠  *Seu veículo é Flex ou Híbrido!*\n\n"
                        "Por favor, especifique qual combustível deseja alterar:\n"
                        "  •  `!alterar km gasolina <valor>`\n"
                        "  •  `!alterar km etanol <valor>`"
                    )
                # Veículo de combustível único — mapeia ao parâmetro correto
                if "etanol" in tipo_comb or "alcool" in tipo_comb:
                    param_nome = "km etanol"
                elif "gnv" in tipo_comb:
                    param_nome = "km m3"
                elif "eletric" in tipo_comb:
                    param_nome = "km kwh"
                else:
                    param_nome = "km gasolina"
            except Exception as exc:
                logger.error(f"[ParametrosService] Erro ao resolver alias km genérico: {exc}")
                return "❌ Erro interno ao identificar o combustível do veículo."

        # Valida contra whitelist
        if param_nome not in PARAM_MAP:
            opcoes = "  |  ".join(f"*{k}*" for k in dict.fromkeys(PARAM_MAP))  # sem duplicatas
            return (
                f"⚠ Parâmetro  *{param_nome}*  não reconhecido.\n\n"
                f"Parâmetros disponíveis:\n{opcoes}\n\n"
                f"Ou envie  *!parametros*  para ver a lista completa."
            )

        coluna, tipo, tabela, label = PARAM_MAP[param_nome]

        # Conversão de tipo com mensagem de erro orientada ao usuário
        try:
            valor_final = ParametrosService._converter_valor(valor_raw, tipo)
        except (InvalidOperation, ValueError):
            tipo_nome = "inteiro (sem casas decimais)" if tipo is int else "número decimal"
            return (
                f"⚠ Valor  *{valor_raw}*  inválido para  *{label}* .\n"
                f"É esperado um valor {tipo_nome}.  Exemplo:  `!alterar {param_nome} "
                f"{'26' if tipo is int else '12,5'}` "
            )

        # ── Persistência: JSONB (rendimento energético) ───────────────────
        if tabela.startswith("jsonb_estoque:"):
            subdict = tabela.split(":")[1]  # "liquido" | "eletricidade" | "gnv"
            return await ParametrosService._alterar_rendimento(
                motorista_id, tenant_id, param_nome, coluna, subdict, label, valor_final
            )

        # ── Persistência: colunas planas (motoristas / veiculos) ──────────
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                if tabela == "motoristas":
                    await conn.execute(
                        f"UPDATE public.motoristas SET {coluna} = $1 WHERE id = $2::uuid;",
                        valor_final, motorista_id,
                    )
                else:  # veiculos
                    await conn.execute(
                        f"UPDATE public.veiculos SET {coluna} = $1 WHERE motorista_id = $2::uuid AND ativo = TRUE;",
                        valor_final, motorista_id,
                    )

            # Invalida o cache de perfil para que a próxima leitura reflita o novo valor
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")

            # Auditoria assíncrona (falha silenciosa — não bloqueia a resposta)
            await ParametrosService._registrar_auditoria(tenant_id, motorista_id, param_nome, coluna, valor_final)

            logger.info(
                f"[ParametrosService] Parâmetro atualizado: motorista={motorista_id} "
                f"tabela={tabela} coluna={coluna} valor={valor_final}"
            )

            valor_fmt = (
                f"R$ {valor_final:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if tipo is Decimal else str(valor_final)
            )
            return (
                f"✅  *{label}*  atualizado com sucesso!\n"
                f"Novo valor:  *{valor_fmt}*\n\n"
                f"_{_TABELA_LABEL[tabela]} recalibrado. O próximo DRE já usará o novo parâmetro._"
            )

        except Exception as exc:
            logger.error(
                f"[ParametrosService] Erro ao persistir parâmetro (motorista={motorista_id} "
                f"coluna={coluna}): {exc}"
            )
            return "❌ Erro interno ao salvar o parâmetro. Verifique o valor e tente novamente."

    @staticmethod
    async def _alterar_rendimento(
        motorista_id: str,
        tenant_id: str,
        param_nome: str,
        coluna: str,
        subdict: str,
        label: str,
        valor_final: Decimal,
    ) -> str:
        """Atualiza uma sub-chave de rendimento dentro do JSONB estoque_financeiro."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, estoque_financeiro FROM public.veiculos "
                    "WHERE motorista_id = $1::uuid AND ativo = TRUE "
                    "ORDER BY created_at DESC LIMIT 1 FOR UPDATE;",
                    motorista_id,
                )
                if not row:
                    return "⚠ Nenhum veículo ativo localizado para alterar o rendimento."

                veiculo_id = str(row["id"])
                raw = row["estoque_financeiro"]
                estoque: dict = _json.loads(raw) if isinstance(raw, str) else (raw or {})

                from services.turno_service import TurnoService
                estoque = TurnoService._garantir_estrutura_estoque(estoque)

                valor_float = float(
                    valor_final.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
                estoque[subdict][coluna] = valor_float

                await conn.execute(
                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                    _json.dumps(estoque), veiculo_id,
                )

            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, param_nome,
                f"estoque.{subdict}.{coluna}", valor_final
            )

            logger.info(
                f"[ParametrosService] Rendimento atualizado: motorista={motorista_id} "
                f"subdict={subdict} coluna={coluna} valor={valor_final}"
            )

            # Formata sem "R$" — unidade varia por parâmetro
            valor_fmt = f"{valor_float:.2f}".replace(".", ",")
            unidade_map = {
                "km_l_gasolina":       "km/L",
                "km_l_etanol":         "km/L",
                "km_kwh":              "km/kWh",
                "km_m3":               "km/m³",
                "capacidade_tanque_l": "L",
                "capacidade_bateria_kwh": "kWh",
            }
            unidade = unidade_map.get(coluna, "")

            # Nota contextual extra para capacidade do tanque (afeta o self-heal)
            nota_extra = ""
            if coluna == "capacidade_tanque_l":
                nota_extra = (
                    "\n_No próximo abastecimento com tanque cheio, o cofre será ancorado "
                    f"a  *{valor_fmt} L*  automaticamente._"
                )
            elif coluna == "capacidade_bateria_kwh":
                nota_extra = (
                    "\n_Na próxima recarga completa, o cofre elétrico será recalibrado "
                    f"para  *{valor_fmt} kWh* ._"
                )

            return (
                f"✅  *{label}*  atualizado com sucesso!\n"
                f"Novo valor:  *{valor_fmt} {unidade}*\n\n"
                f"_{_TABELA_LABEL['jsonb_estoque:' + subdict]} recalibrado. "
                f"O próximo DRE já usará o novo valor._{nota_extra}"
            )

        except Exception as exc:
            logger.error(
                f"[ParametrosService] Erro ao alterar rendimento (motorista={motorista_id} "
                f"coluna={coluna}): {exc}"
            )
            return "❌ Erro interno ao salvar o rendimento. Verifique o valor e tente novamente."

    # ------------------------------------------------------------------
    # Gestão de despesas fixas mensais
    # ------------------------------------------------------------------

    @staticmethod
    async def _listar_despesas_fixas(motorista_id: str) -> str:
        """Lista as despesas fixas mensais ativas do motorista."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                rows = await conn.fetch(
                    """
                    SELECT nome, valor_mensal, dias_trabalho_previstos, valor_pro_rata_diario
                    FROM public.despesas_fixas_mensais
                    WHERE motorista_id = $1::uuid AND ativo = TRUE
                    ORDER BY valor_mensal DESC;
                    """,
                    motorista_id,
                )
            if not rows:
                return (
                    "📌  *Nenhuma despesa fixa cadastrada ainda.*\n\n"
                    "Para adicionar, envie:\n"
                    "  *!adicionar despesa <nome> <R$/mês> <dias úteis>*\n"
                    "_Ex: !adicionar despesa seguro 180 26_"
                )
            total_pro_rata = sum(float(r["valor_pro_rata_diario"]) for r in rows)
            linhas = ["📌  *Despesas Fixas Mensais Ativas:*\n"]
            for r in rows:
                nome   = r["nome"]
                mensal = float(r["valor_mensal"])
                diario = float(r["valor_pro_rata_diario"])
                linhas.append(
                    f"• {nome}:  *R$ {mensal:.2f}/mês*  (≈ R$ {diario:.2f}/dia)"
                )
            linhas.append(f"\n*Total pro-rata diário: R$ {total_pro_rata:.2f}*")
            linhas.append(
                "\n_Para remover, envie:  *!remover despesa <nome>*_\n"
                "_Para adicionar:  *!adicionar despesa <nome> <R$/mês> <dias>*_"
            )
            return "\n".join(linhas)
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao listar despesas fixas (motorista={motorista_id}): {exc}")
            return "❌ Erro ao buscar despesas fixas. Tente novamente em instantes."

    @staticmethod
    async def _obter_ou_criar_caixa(conn, motorista_id: str, nome_caixa: str) -> str:
        """Retorna o UUID da caixa com o nome informado, criando-a se não existir."""
        row = await conn.fetchrow(
            "SELECT id FROM public.caixas_provisao "
            "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
            motorista_id, nome_caixa,
        )
        if row:
            return str(row["id"])
        new_id = await conn.fetchval(
            """
            INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual)
            VALUES ($1::uuid, $2, 0.00)
            ON CONFLICT (motorista_id, nome_caixa) DO UPDATE SET nome_caixa = EXCLUDED.nome_caixa
            RETURNING id;
            """,
            motorista_id, nome_caixa,
        )
        return str(new_id)

    @staticmethod
    async def _adicionar_despesa_fixa(
        motorista_id: str, tenant_id: str, nome: str, valor_mensal: Decimal, dias: int
    ) -> str:
        """Insere ou reativa uma despesa fixa mensal e vincula à caixinha de provisão correspondente."""
        if valor_mensal <= 0:
            return "⚠ O valor mensal deve ser maior que zero."
        if not (1 <= dias <= 31):
            return "⚠ O número de dias úteis deve estar entre 1 e 31."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # Garante que existe uma caixa de provisão com o mesmo nome da despesa
                caixa_id = await ParametrosService._obter_ou_criar_caixa(conn, motorista_id, nome)
                # Verifica se já existe (mesmo nome, case-insensitive) para reativar
                existing = await conn.fetchrow(
                    "SELECT id, ativo FROM public.despesas_fixas_mensais "
                    "WHERE motorista_id = $1::uuid AND lower(nome) = lower($2);",
                    motorista_id, nome,
                )
                if existing:
                    await conn.execute(
                        "UPDATE public.despesas_fixas_mensais "
                        "SET valor_mensal = $1, dias_trabalho_previstos = $2, ativo = TRUE, caixa_id = $3::uuid "
                        "WHERE id = $4::uuid;",
                        valor_mensal, dias, caixa_id, str(existing["id"]),
                    )
                    acao = "reativada e atualizada"
                else:
                    await conn.execute(
                        """
                        INSERT INTO public.despesas_fixas_mensais
                            (motorista_id, nome, valor_mensal, dias_trabalho_previstos, dia_vencimento, caixa_id)
                        VALUES ($1::uuid, $2, $3, $4, 1, $5::uuid);
                        """,
                        motorista_id, nome, valor_mensal, dias, caixa_id,
                    )
                    acao = "adicionada"

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"despesa_fixa_{nome}", "valor_mensal", valor_mensal
            )
            pro_rata = float(valor_mensal / Decimal(str(dias)))
            return (
                f"✅  *Despesa fixa {acao}!*\n"
                f"• Nome:  *{nome}*\n"
                f"• Valor mensal:  *R$ {float(valor_mensal):.2f}*\n"
                f"• Pro-rata diário:  *R$ {pro_rata:.2f}*  (base: {dias} dias úteis)\n"
                f"• Caixinha vinculada:  *{nome}*  _(aportes automáticos a cada fechamento)_\n\n"
                f"_Esse custo será deduzido automaticamente em cada fechamento de turno._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao adicionar despesa fixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao salvar a despesa fixa. Verifique os dados e tente novamente."

    @staticmethod
    async def _listar_caixas(motorista_id: str) -> str:
        """Lista todas as caixas de provisão ativas com seus saldos atuais."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                rows = await conn.fetch(
                    """
                    SELECT cp.nome_caixa, cp.saldo_atual,
                           COALESCE(SUM(dfm.valor_pro_rata_diario), 0) AS aporte_diario
                    FROM public.caixas_provisao cp
                    LEFT JOIN public.despesas_fixas_mensais dfm
                        ON dfm.caixa_id = cp.id AND dfm.ativo = TRUE
                    WHERE cp.motorista_id = $1::uuid
                    GROUP BY cp.id, cp.nome_caixa, cp.saldo_atual
                    ORDER BY cp.saldo_atual DESC;
                    """,
                    motorista_id,
                )
            if not rows:
                return (
                    "📦  *Nenhuma caixa de provisão cadastrada ainda.*\n\n"
                    "As caixas são criadas automaticamente quando você adiciona uma despesa fixa.\n"
                    "Ou crie manualmente com:\n"
                    "  *!criar caixa <nome>*"
                )
            linhas = ["📦  *Caixas de Provisão:*\n"]
            total_saldo = 0.0
            for r in rows:
                saldo = float(r["saldo_atual"])
                total_saldo += saldo
                aporte = float(r["aporte_diario"])
                aporte_str = f"  _(+R$ {aporte:.2f}/dia)_" if aporte > 0 else ""
                linhas.append(f"• {r['nome_caixa']}:  *R$ {saldo:.2f}*{aporte_str}")
            linhas.append(f"\n*Total em reserva: R$ {total_saldo:.2f}*")
            linhas.append(
                "\n_Para retirar:  *!retirar caixa <nome> <valor>*_\n"
                "_Para criar nova:  *!criar caixa <nome>*_"
            )
            return "\n".join(linhas)
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao listar caixas (motorista={motorista_id}): {exc}")
            return "❌ Erro ao buscar caixas de provisão. Tente novamente em instantes."

    @staticmethod
    async def _criar_caixa(motorista_id: str, tenant_id: str, nome: str) -> str:
        """Cria uma caixa de provisão avulsa (sem despesa fixa vinculada)."""
        nome = nome.strip()[:60]
        if not nome:
            return "⚠ Informe um nome para a caixa. Ex:  *!criar caixa viagem*"
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                existing = await conn.fetchval(
                    "SELECT id FROM public.caixas_provisao WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
                    motorista_id, nome,
                )
                if existing:
                    saldo = await conn.fetchval(
                        "SELECT saldo_atual FROM public.caixas_provisao WHERE id = $1::uuid;",
                        str(existing),
                    )
                    return (
                        f"ℹ️  A caixa  *{nome}*  já existe com saldo de  *R$ {float(saldo):.2f}* .\n"
                        f"_Os aportes ocorrem automaticamente a cada fechamento de turno._\n"
                        f"_Para sacar, use  *!retirar caixa {nome} <valor>*_"
                    )
                await conn.execute(
                    "INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual) VALUES ($1::uuid, $2, 0.00);",
                    motorista_id, nome,
                )
            await ParametrosService._registrar_auditoria(tenant_id, motorista_id, f"criar_caixa_{nome}", "saldo_atual", 0)
            return (
                f"✅  *Caixa  *{nome}*  criada com saldo R$ 0,00!*\n\n"
                f"_Para vincular a uma despesa fixa, use:_\n"
                f"  *!adicionar despesa {nome} <R$/mês> <dias>*"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao criar caixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao criar a caixa. Tente novamente."

    @staticmethod
    async def _retirar_caixa(motorista_id: str, tenant_id: str, nome: str, valor: Decimal) -> str:
        """Registra uma retirada (saque real) de uma caixa de provisão."""
        if valor <= 0:
            return "⚠ O valor da retirada deve ser maior que zero."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, saldo_atual FROM public.caixas_provisao "
                    "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
                    motorista_id, nome,
                )
                if not row:
                    return (
                        f"⚠ Nenhuma caixa chamada  *{nome}*  encontrada.\n"
                        f"Envie  *!caixas*  para ver a lista."
                    )
                saldo_atual = Decimal(str(row["saldo_atual"]))
                if valor > saldo_atual:
                    return (
                        f"⚠  *Saldo insuficiente!*\n"
                        f"• Caixa  *{nome}* :  *R$ {float(saldo_atual):.2f}*\n"
                        f"• Retirada solicitada:  *R$ {float(valor):.2f}*\n\n"
                        f"_Você só pode retirar até o saldo disponível._"
                    )
                novo_saldo = saldo_atual - valor
                await conn.execute(
                    "UPDATE public.caixas_provisao SET saldo_atual = $1 WHERE id = $2::uuid;",
                    novo_saldo, str(row["id"]),
                )
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"retirada_caixa_{nome}", "saldo_atual", -valor
            )
            return (
                f"✅  *Retirada registrada!*\n"
                f"• Caixa:  *{nome}*\n"
                f"• Valor retirado:  *R$ {float(valor):.2f}*\n"
                f"• Novo saldo:  *R$ {float(novo_saldo):.2f}*\n\n"
                f"_Use este dinheiro para pagar a despesa real quando chegar o vencimento._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao retirar da caixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao processar a retirada. Tente novamente."

    @staticmethod
    async def _remover_despesa_fixa(motorista_id: str, tenant_id: str, nome: str) -> str:
        """Desativa (soft-delete) uma despesa fixa pelo nome."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                result = await conn.execute(
                    "UPDATE public.despesas_fixas_mensais SET ativo = FALSE "
                    "WHERE motorista_id = $1::uuid AND lower(nome) = lower($2) AND ativo = TRUE;",
                    motorista_id, nome,
                )
            # asyncpg retorna "UPDATE N" como string
            n = int(result.split()[-1]) if result else 0
            if n == 0:
                return (
                    f"⚠ Nenhuma despesa fixa ativa com o nome  *{nome}*  foi encontrada.\n"
                    f"Envie  *!despesas fixas*  para ver a lista atual."
                )
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"remover_despesa_{nome}", "ativo", False
            )
            return (
                f"✅  *Despesa fixa  *{nome}*  removida com sucesso!*\n"
                f"_Ela não será mais deduzida nos próximos fechamentos de turno._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao remover despesa fixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao remover a despesa fixa. Tente novamente em instantes."
