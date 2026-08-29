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

Para listar os parâmetros disponíveis:
    !parametros
"""

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
    "meta":          ("meta_mensal_faturamento",  Decimal, "motoristas", "Meta Mensal (R$)"),   # sinônimo
    "dias uteis":    ("dias_uteis_mes",            int,     "motoristas", "Dias Úteis/Mês"),
    # ── Veículos ────────────────────────────────────────────────────────────
    "aluguel":       ("custo_aluguel_semanal",     Decimal, "veiculos",   "Aluguel Semanal (R$)"),
    "franquia":      ("franquia_km_semanal",       Decimal, "veiculos",   "Franquia KM Semanal"),
}

# Nomes de exibição das tabelas para a mensagem de confirmação
_TABELA_LABEL = {
    "motoristas": "perfil do motorista",
    "veiculos":   "veículo ativo",
}


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
            linhas = ["📋  *Parâmetros ajustáveis via !alterar:*\n"]
            vistos: set[str] = set()
            for alias, (_, _, _, label) in PARAM_MAP.items():
                if label not in vistos:
                    linhas.append(f"  • `!alterar {alias} <valor>`  →  {label}")
                    vistos.add(label)
            linhas.append(
                "\n⛽  *Ajuste de Estoque Virtual:*\n"
                "  • `!ajustar estoque litros <qtd>`  →  Corrigir litros no cofre\n"
                "  • `!ajustar estoque kwh <qtd>`     →  Corrigir kWh no cofre\n"
                "  • `!ajustar estoque m3 <qtd>`      →  Corrigir m³ de GNV no cofre\n"
                "\n_Exemplo:_  `!alterar meta mensal 12000`\n"
                "_Exemplo:_  `!ajustar estoque litros 35`"
            )
            return "\n".join(linhas)

        # ── Comando !alterar ─────────────────────────────────────────────
        match = ParametrosService._RE_ALTERAR.match(texto)
        if not match:
            # Não é um comando '!' reconhecido — devolve None para o orquestrador
            return None

        param_nome = match.group(1).strip().lower()
        valor_raw  = match.group(2)

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
                f"{'26' if tipo is int else '12000,00'}` "
            )

        # Persistência com RLS
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
