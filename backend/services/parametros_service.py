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
    "piso km":       ("piso_ganho_km",             Decimal, "motoristas", "Piso de Ganho por KM (R$/km)"),
    "piso hora":     ("piso_ganho_hora",           Decimal, "motoristas", "Piso de Ganho por Hora (R$/h)"),
    "meta horas":    ("meta_horas_diarias",        Decimal, "motoristas", "Meta de Horas Diárias"),
    "meta km":       ("meta_km_diarios",           Decimal, "motoristas", "Meta de KM Diários"),
    # ── Veículos (colunas planas) ────────────────────────────────────────────
    "aluguel":       ("custo_aluguel_semanal",     Decimal, "veiculos",   "Aluguel Semanal (R$)"),
    "franquia":      ("franquia_km_semanal",       Decimal, "veiculos",   "Franquia KM Semanal (km)"),
    "km excedente":  ("valor_km_excedente",        Decimal, "veiculos",   "Preço KM Excedente (R$/km)"),
    "excedente":     ("valor_km_excedente",        Decimal, "veiculos",   "Preço KM Excedente (R$/km)"),
    # dias de trabalho por semana — afeta custo diário (aluguel/dias) e exibição de escala
    "dias semana":   ("dias_trabalho_semana",      int,     "veiculos",   "Dias de Trabalho por Semana"),
    "dias por semana": ("dias_trabalho_semana",    int,     "veiculos",   "Dias de Trabalho por Semana"),
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
    # Captura o nome do parâmetro e o valor bruto como uma única string.
    # O valor bruto é limpo por _limpar_valor_bruto() antes de ser convertido.
    # Aceita qualquer sufixo após o número (km, /km, /h, kWh, L, m³, etc.)
    # para que o motorista possa escrever naturalmente.
    # ------------------------------------------------------------------
    _RE_ALTERAR = re.compile(
        r"^!alterar\s+(.+?)\s+(R\$\s*[\d.,\s]+[\d].*|[\d][\d.,\s]*.*)$",
        re.IGNORECASE,
    )

    @staticmethod
    def _limpar_valor_bruto(raw: str) -> str:
        """Extrai e normaliza o número de uma string de valor digitada pelo usuário.

        Trata os padrões brasileiros mais comuns:
          "R$2,2"       → "2.2"
          "R$ 1.500,50" → "1500.50"   (milhar BR com vírgula decimal)
          "1500"        → "1500"
          "1.500"       → "1500"       (milhar BR sem decimal)
          "1,5"         → "1.5"
          "12.5"        → "12.5"       (decimal US)
          "1500 km"     → "1500"
          "2,50/km"     → "2.50"
        """
        # 1. Remove prefixo monetário e espaços
        s = re.sub(r"(?i)R\$\s*", "", raw).strip()
        # 2. Remove sufixo de unidade (qualquer coisa após o último dígito)
        s = re.sub(r"[^\d.,]+$", "", s).strip()
        # 3. Resolve ambiguidade milhar vs decimal (lógica idêntica ao converter_para_float)
        if "," in s and "." in s:
            if s.find(".") < s.find(","):   # padrão BR: 1.000,50
                s = s.replace(".", "").replace(",", ".")
            else:                            # padrão US: 1,000.50
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        # 4. Remove pontos que ainda sobraram sem parte decimal (separador de milhar puro)
        # ex: "1.500" → depois do passo 3 ainda é "1.500" se não havia vírgula
        # Detecta: string tem ponto mas não é decimal (parte após ponto tem 3 dígitos)
        if "." in s:
            partes = s.split(".")
            if len(partes) == 2 and len(partes[1]) == 3 and partes[1].isdigit():
                s = s.replace(".", "")  # era separador de milhar
        return s

    @staticmethod
    def _converter_valor(raw: str, tipo: type):
        """Converte a string bruta do usuário para o tipo esperado pela coluna do banco."""
        normalizado = ParametrosService._limpar_valor_bruto(raw)
        if not normalizado:
            raise ValueError(f"Valor '{raw}' não contém um número reconhecível.")
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
                    "SELECT id, estoque_financeiro FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY selecionado DESC, created_at DESC LIMIT 1;",
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

                novo_dec = novo_valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Sincronização holística: preserva o CMP unitário e sub-litros
                if fonte == "litros":
                    d_liq = estoque.get("liquido", {})
                    old_q = Decimal(str(d_liq.get("litros", 0.0)))
                    old_c = Decimal(str(d_liq.get("custo_total", 0.0)))
                    p_gas = Decimal(str(d_liq.get("gasolina_proporcao", 1.0)))
                    p_eta = Decimal(str(d_liq.get("etanol_proporcao", 0.0)))
                    cmp_unit = (old_c / old_q) if (old_q > Decimal("0") and old_c > Decimal("0")) else Decimal("5.85")
                    
                    d_liq["litros"] = float(novo_dec)
                    d_liq["gasolina_litros"] = float((novo_dec * p_gas).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    d_liq["etanol_litros"]   = float((novo_dec * p_eta).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    d_liq["custo_total"]     = float((novo_dec * cmp_unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    estoque["liquido"] = d_liq

                elif fonte == "kwh":
                    d_ele = estoque.get("eletricidade", {})
                    old_q = Decimal(str(d_ele.get("kwh", 0.0)))
                    old_c = Decimal(str(d_ele.get("custo_total", 0.0)))
                    cmp_unit = (old_c / old_q) if (old_q > Decimal("0") and old_c > Decimal("0")) else Decimal("1.20")
                    d_ele["kwh"] = float(novo_dec)
                    d_ele["custo_total"] = float((novo_dec * cmp_unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    estoque["eletricidade"] = d_ele

                elif fonte == "m3":
                    d_gnv = estoque.get("gnv", {})
                    old_q = Decimal(str(d_gnv.get("m3", 0.0)))
                    old_c = Decimal(str(d_gnv.get("custo_total", 0.0)))
                    cmp_unit = (old_c / old_q) if (old_q > Decimal("0") and old_c > Decimal("0")) else Decimal("4.50")
                    d_gnv["m3"] = float(novo_dec)
                    d_gnv["custo_total"] = float((novo_dec * cmp_unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    estoque["gnv"] = d_gnv

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
                f"_O CMP (custo médio por unidade) e a proporção de combustível foram mantidos coerentes._"
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
                "  •  *!alterar piso km <R$/km>*    →  Piso mínimo de ganho por km rodado",
                "  •  *!alterar piso hora <R$/h>*   →  Piso mínimo de ganho por hora trabalhada",
                "  •  *!alterar meta horas <h>*     →  Meta de horas diárias de trabalho",
                "  •  *!alterar meta km <km>*        →  Meta de KM diários a rodar",
                "",
                "👤  *Perfil e Veículo:*",
                "  •  *!alterar nome <novo nome>*          →  Atualizar nome cadastrado",
                "  •  *!alterar nome social <apelido>*     →  Nome de chamada no chat",
                "  •  *!alterar modelo <modelo>*           →  Modelo do veículo ativo",
                "  •  *!alterar placa <placa>*             →  Placa do veículo ativo",
                "  •  *!alterar combustivel <tipo>*        →  Tipo de combustível",
                "     _Tipos: gasolina | etanol | flex | hibrido | eletrico | gnv_",
                "",
                "🚗  *Contrato e Veículo:*",
                "  •  *!alterar aluguel <R$/sem>*    →  Custo semanal do aluguel (carros alugados)",
                "  •  *!alterar franquia <km/sem>*   →  Franquia de KM semanal (carros alugados)",
                "  •  *!alterar km excedente <R$/km>* → Preço do KM além da franquia (carros alugados)",
                "  •  *!alterar tanque <litros>*     →  Capacidade do tanque (afeta self-heal)",
                "  •  *!alterar bateria <kWh>*       →  Capacidade da bateria elétrica",
                "  •  *!alterar dias semana <1-7>*   →  Dias trabalhados por semana (afeta custo diário)",
                "  •  *!alterar escala <descrição>*  →  Escala semanal (ex: seg a sab, ter a dom)",
                "     _Ex: !alterar escala seg a sab_  →  Seg a Sáb (6 dias)_",
                "     _Ex: !alterar escala seg a sex_  →  Seg a Sex (5 dias)_",
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
                "  •  *!adicionar despesa <nome> <R$/mês> [dias] [venc1] [venc2...]*",
                "     _Ex: !adicionar despesa seguro 180_           _(dias=auto, vence dia 1)_",
                "     _Ex: !adicionar despesa seguro 180 26 5_      _(26 dias, vence dia 5)_",
                "     _Ex: !adicionar despesa cartao 800 26 5 20_   _(vence dias 5 e 20)_",
                "  •  *!adicionar despesa semanal <nome> <R$/mês> [dia_semana]*",
                "     _Ex: !adicionar despesa semanal aluguel 400_  _(toda segunda-feira)_",
                "     _Ex: !adicionar despesa semanal aluguel 400 5_ _(toda sexta-feira)_",
                "     _Dias da semana: 1=Seg  2=Ter  3=Qua  4=Qui  5=Sex  6=Sáb  7=Dom_",
                "  •  *!remover despesa <nome>*        → Desativar despesa pelo nome",
                "",
                "📦  *Caixas de Provisão (sinking fund):*",
                "  •  *!caixas*                              → Saldos + barras de progresso",
                "  •  *!criar caixa <nome> <meta>*           → Ex: !criar caixa pneu 500",
                "  •  *!criar caixa <nome>*                  → Sem meta (acumulação livre)",
                "  •  *!definir meta caixa <nome> <valor>*   → Altera meta de caixa existente",
                "  •  *!remover meta caixa <nome>*           → Remove o teto",
                "  •  *!retirar caixa <nome> <valor>*        → Sacar quando a despesa chegar",
                "     _Ex: !retirar caixa pneu 480_",
                "  •  *!excluir caixa <nome>*                → Apagar a caixa permanentemente",
                "",
                "🚗  *Frota (múltiplos veículos):*",
                "  •  *!veiculos*                            → Listar todos os veículos cadastrados",
                "  •   !cadastrar veiculo                    → Cadastra um novo veículo",
                "  •  *!selecionar <placa>*                  → Trocar o veículo ativo (fora do turno)",
                "     _Ex: !selecionar ABC1234_",
                "",
                "🤝  *Contrato:*",
                "  •  *atualizar contrato <Locadora> <Aluguel> <Franquia> [dias] [vencimento]*",
                "     _Ex mensal:  atualizar contrato Zarp 1020 1505_",
                "     _Ex semanal: atualizar contrato Pai 250 0 6 toda terça_",
                "     _Ex mensal múltiplo: atualizar contrato Zarp 1020 1505 6 dia 5 20_",
                "     _Proprio/Financiado: atualizar contrato Proprietario 90_",
                "",
                "_Exemplos:_",
                "  *!alterar meta mensal 12000*",
                "  *!alterar tanque 50*",
                "  *!alterar km excedente 0,75*",
                "  *!adicionar despesa internet 120*",
                "  *!caixas*",
                "  *!retirar caixa internet 120*",
            ]
            return "\n".join(linhas)

        # ── Gestão de despesas fixas mensais ─────────────────────────────
        if re.match(r"^!despesas?\s+fix", texto, re.IGNORECASE):
            return await ParametrosService._listar_despesas_fixas(motorista_id)

        # ── Despesa SEMANAL: !adicionar despesa semanal <nome> <valor> [dia_iso] ──
        # Formato: !adicionar despesa semanal <nome> <valor> [1-7]
        # Dia da semana ISO: 1=Segunda ... 7=Domingo. Default: 1 (segunda).
        # Ex: !adicionar despesa semanal diaria 80
        # Ex: !adicionar despesa semanal diaria 80 5   (toda sexta)
        match_semanal = re.match(
            r"^!adicionar\s+despesa\s+semanal\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)(?:\s+([1-7]))?\s*$",
            texto, re.IGNORECASE,
        )
        if match_semanal:
            nome_desp  = match_semanal.group(1).strip()[:50]
            valor_desp = Decimal(match_semanal.group(2).replace(",", "."))
            dia_iso    = int(match_semanal.group(3)) if match_semanal.group(3) else 1
            return await ParametrosService._adicionar_despesa_semanal(
                motorista_id, tenant_id, nome_desp, valor_desp, dia_iso
            )

        # ── Despesa ÚNICA: !adicionar despesa [unica] <nome> <valor> [unica] [dia <n>] [a partir de DD/MM] ──
        # Suporta:
        #   !adicionar despesa Manutencao 500 unica dia 15
        #   !adicionar despesa unica Manutencao 500 dia 15
        #   !adicionar despesa Manutencao 500 dia 15 unica
        match_unica_ant = re.match(
            r"^!adicionar\s+despesa\s+[uú]nica\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)(.*)?$",
            texto, re.IGNORECASE,
        )
        match_unica_pos = re.match(
            r"^!adicionar\s+despesa\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)\s+[uú]nica(.*)?$",
            texto, re.IGNORECASE,
        )
        match_unica = match_unica_ant or match_unica_pos
        if match_unica:
            nome_desp  = match_unica.group(1).strip()[:50]
            try:
                valor_desp = Decimal(match_unica.group(2).replace(",", "."))
            except Exception:
                return "⚠ Valor inválido. Ex:  *!adicionar despesa Manutencao 500 unica dia 15*"
            resto      = (match_unica.group(3) or "").lower().strip()
            # Extrai dia de vencimento se informado ("dia 15" ou número isolado)
            _dia_m = re.search(r'\bdia\s+(\d{1,2})\b', resto)
            if not _dia_m:
                _dia_m = re.search(r'\b(\d{1,2})\b', resto)
            dias_venc_u = [int(_dia_m.group(1))] if _dia_m else [1]
            invalidos = [d for d in dias_venc_u if not (1 <= d <= 31)]
            if invalidos:
                return f"⚠ Dia inválido: {invalidos[0]}. Use um valor entre 1 e 31."
            # Extrai data de início se informada ("a partir de DD/MM" ou "DD/MM/AAAA")
            _data_m = re.search(r'(?:a\s+partir\s+de\s+|em\s+)?(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?', resto)
            data_inicio_u = None
            if _data_m:
                from datetime import date as _dtu
                try:
                    _d, _m = int(_data_m.group(1)), int(_data_m.group(2))
                    _a = int(_data_m.group(3)) if _data_m.group(3) else _dtu.today().year
                    _a = _a + 2000 if _a < 100 else _a
                    data_inicio_u = _dtu(_a, _m, _d)
                except (ValueError, TypeError):
                    data_inicio_u = None
            return await ParametrosService._adicionar_despesa_fixa(
                motorista_id, tenant_id, nome_desp, valor_desp,
                None, dias_venc_u,
                parcelas_totais=1, data_inicio=data_inicio_u,
                valor_total=valor_desp,
            )

        # ── Despesa PARCELADA: !adicionar despesa <nome> <valor> [em] <N> parcelas [dia <n> | quinzenal] ──
        # Ex: !adicionar despesa seguro 1200 em 4 parcelas todo dia 10
        # Ex: !adicionar despesa pneus 800 em 2 parcelas quinzenais
        match_parcelas = re.match(
            r"^!adicionar\s+despesa\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)\s+(?:em\s+)?(\d+)\s+parcelas?(.*)?$",
            texto, re.IGNORECASE,
        )
        if match_parcelas:
            nome_desp   = match_parcelas.group(1).strip()[:50]
            try:
                valor_total = Decimal(match_parcelas.group(2).replace(",", "."))
                n_parcelas  = int(match_parcelas.group(3))
            except Exception:
                return "⚠ Formato inválido. Ex:  *!adicionar despesa seguro 1200 em 4 parcelas todo dia 10*"
            if n_parcelas < 1:
                return "⚠ O número de parcelas deve ser pelo menos 1."
            if valor_total <= 0:
                return "⚠ O valor total deve ser maior que zero."
            resto_p = (match_parcelas.group(4) or "").lower().strip()

            is_quinzenal = "quinzenal" in resto_p or "quinzena" in resto_p
            if is_quinzenal:
                dias_venc_p = [1, 15]
                freq_dias = 15
                # No quinzenal com 2 parcelas no mesmo mês, o valor mensal a provisionar
                # é o valor total das duas parcelas (ex: R$ 800), e o ReminderService dividirá
                # pelos 2 vencimentos gerando exatamente R$ 400 por quinzena.
                if n_parcelas == 2:
                    valor_mensal_calc = valor_total
                else:
                    valor_parcela_unit = (valor_total / Decimal(str(n_parcelas))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    valor_mensal_calc = valor_parcela_unit * 2
            else:
                freq_dias = 30
                _dia_mp = re.search(r'\bdia\s+(\d{1,2})\b', resto_p)
                if not _dia_mp:
                    _dia_mp = re.search(r'\b(\d{1,2})\b', resto_p)
                dias_venc_p = [int(_dia_mp.group(1))] if _dia_mp else [1]
                # Mensal: cada mês provisiona 1 parcela
                valor_mensal_calc = (valor_total / Decimal(str(n_parcelas))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

            invalidos = [d for d in dias_venc_p if not (1 <= d <= 31)]
            if invalidos:
                return f"⚠ Dia inválido: {invalidos[0]}. Use um valor entre 1 e 31."

            return await ParametrosService._adicionar_despesa_fixa(
                motorista_id, tenant_id, nome_desp, valor_mensal_calc,
                None, dias_venc_p,
                parcelas_totais=n_parcelas, data_inicio=None,
                valor_total=valor_total, frequencia_dias=freq_dias,
            )

        # Formato: !adicionar despesa <nome> <valor> [dias_uteis] [venc1] [venc2] ...
        # Exemplos:
        #   !adicionar despesa seguro 180              → dias=auto, vence dia 1
        #   !adicionar despesa seguro 180 26           → dias=26, vence dia 1
        #   !adicionar despesa seguro 180 26 5         → dias=26, vence dia 5
        #   !adicionar despesa cartao 800 26 5 20      → dias=26, vence dias 5 e 20
        match_add = re.match(
            r"^!adicionar\s+despesa\s+(.+?)\s+([\d]+(?:[.,][\d]+)?)((?:\s+\d+)*)\s*$",
            texto, re.IGNORECASE,
        )
        if match_add:
            nome_desp  = match_add.group(1).strip()[:50]
            valor_desp = Decimal(match_add.group(2).replace(",", "."))
            # Grupo 3: zero ou mais números inteiros separados por espaço
            nums = [int(x) for x in match_add.group(3).split() if x.isdigit()]

            # Interpretação:
            #   sem números          → dias=None (auto), vencimentos=[1]
            #   1 número  (ex: 26)   → dias=26, vencimentos=[1]
            #   2+ números (ex: 26 5 20) → primeiro=dias, resto=vencimentos
            if len(nums) == 0:
                dias_desp_raw  = None
                dias_venc      = [1]
            elif len(nums) == 1:
                dias_desp_raw  = nums[0]
                dias_venc      = [1]
            else:
                dias_desp_raw  = nums[0]
                dias_venc      = nums[1:]

            # Valida cada vencimento
            invalidos = [d for d in dias_venc if not (1 <= d <= 31)]
            if invalidos:
                return f"⚠ Dia(s) de vencimento inválido(s): {invalidos}. Use valores entre 1 e 31."
            # Deduplica e ordena
            dias_venc = sorted(set(dias_venc))

            return await ParametrosService._adicionar_despesa_fixa(
                motorista_id, tenant_id, nome_desp, valor_desp, dias_desp_raw, dias_venc
            )

        match_rem = re.match(r"^!remover\s+despesa\s+(.+)$", texto, re.IGNORECASE)
        if match_rem:
            nome_desp = match_rem.group(1).strip()[:50]
            return await ParametrosService._remover_despesa_fixa(motorista_id, tenant_id, nome_desp)

        # ── Gestão de caixas de provisão ──────────────────────────────────
        if re.match(r"^!caixas?\b", texto, re.IGNORECASE):
            return await ParametrosService._listar_caixas(motorista_id)

        # !criar caixa <nome> [meta]   — meta é opcional
        match_criar = re.match(
            r"^!criar\s+caixa\s+(.+?)\s+(R\$\s*[\d.,]+|[\d.,]+)\s*$",
            texto, re.IGNORECASE,
        )
        if match_criar:
            nome_cx = match_criar.group(1).strip()[:60]
            try:
                meta_cx = Decimal(ParametrosService._limpar_valor_bruto(match_criar.group(2)))
            except Exception:
                return "⚠ Valor de meta inválido. Ex:  *!criar caixa pneu 500*"
            return await ParametrosService._criar_caixa(motorista_id, tenant_id, nome_cx, meta_cx)

        match_criar_sem_meta = re.match(r"^!criar\s+caixa\s+(\S.*)$", texto, re.IGNORECASE)
        if match_criar_sem_meta:
            nome_cx = match_criar_sem_meta.group(1).strip()[:60]
            # só aceita se não termina em número (caso contrário o regex acima teria casado)
            return await ParametrosService._criar_caixa(motorista_id, tenant_id, nome_cx, None)

        # !definir meta caixa <nome> <valor>   (ou !remover meta caixa <nome> para zerar)
        match_def_meta = re.match(
            r"^!definir\s+meta\s+caixa\s+(.+?)\s+(R\$\s*[\d.,]+|[\d.,]+)\s*$",
            texto, re.IGNORECASE,
        )
        if match_def_meta:
            nome_cx = match_def_meta.group(1).strip()[:60]
            try:
                meta_cx = Decimal(ParametrosService._limpar_valor_bruto(match_def_meta.group(2)))
            except Exception:
                return "⚠ Valor inválido. Ex:  *!definir meta caixa pneu 500*"
            return await ParametrosService._definir_meta_caixa(motorista_id, tenant_id, nome_cx, meta_cx)

        match_rem_meta = re.match(r"^!remover\s+meta\s+caixa\s+(.+)$", texto, re.IGNORECASE)
        if match_rem_meta:
            nome_cx = match_rem_meta.group(1).strip()[:60]
            return await ParametrosService._definir_meta_caixa(motorista_id, tenant_id, nome_cx, None)

        match_retirar = re.match(
            r"^!retirar\s+caixa\s+(.+?)\s+(R\$\s*[\d.,]+|[\d.,]+)\s*$",
            texto, re.IGNORECASE,
        )
        if match_retirar:
            nome_cx = match_retirar.group(1).strip()[:60]
            try:
                valor_cx = Decimal(ParametrosService._limpar_valor_bruto(match_retirar.group(2)))
            except Exception:
                return "⚠ Valor inválido. Ex:  *!retirar caixa seguro 180*"
            return await ParametrosService._retirar_caixa(motorista_id, tenant_id, nome_cx, valor_cx)

        match_excluir = re.match(r"^!excluir\s+caixa\s+(\S.*)$", texto, re.IGNORECASE)
        if match_excluir:
            nome_cx = match_excluir.group(1).strip()[:60]
            return await ParametrosService._excluir_caixa(motorista_id, tenant_id, nome_cx)

        # Retirada sem valor — informa o formato correto com nome da caixa pré-preenchido
        match_retirar_sem_valor = re.match(r"^!retirar\s+caixa\s+(.+)$", texto, re.IGNORECASE)
        if match_retirar_sem_valor:
            nome_cx = match_retirar_sem_valor.group(1).strip()[:60]
            return (
                f"⚠  *Informe também o valor da retirada.*\n\n"
                f"Exemplo:\n"
                f"  👉  `!retirar caixa {nome_cx} <valor>`\n\n"
                f"_Ex: `!retirar caixa {nome_cx} 180`_"
            )

        # ── Comando !atualizar contrato — alias com ! para o fluxo do orchestrator ──
        # Normaliza para o formato sem ! e deixa cair no catch-all do orchestrator.
        # Retorna None para que o orchestrator intercepte via is_contrato.
        if re.match(r"^!atualizar\s+contrato\b", texto, re.IGNORECASE):
            return None  # deixa o orchestrator processar como "atualizar contrato"

        # ── Comando !alterar nome / !alterar nome social ──────────────────
        # Aceita: !alterar nome <valor>  e  !alterar nome social <valor>
        match_nome = re.match(r"^!alterar\s+nome(?:\s+social)?\s+(.+)$", texto, re.IGNORECASE)
        if match_nome:
            return await ParametrosService._alterar_perfil_texto(
                motorista_id, tenant_id,
                campo=("nome_social" if "social" in texto.lower() else "nome"),
                valor=match_nome.group(1).strip()[:150],
                label=("Nome Social (apelido)" if "social" in texto.lower() else "Nome Cadastrado"),
            )

        # ── Comando !alterar modelo / !alterar placa ──────────────────────
        match_veiculo_texto = re.match(
            r"^!alterar\s+(modelo|placa)\s+(.+)$", texto, re.IGNORECASE
        )
        if match_veiculo_texto:
            campo_vt  = match_veiculo_texto.group(1).lower()
            valor_vt  = match_veiculo_texto.group(2).strip()
            if campo_vt == "placa":
                valor_vt = re.sub(r'[^A-Za-z0-9]', '', valor_vt).upper()
                if len(valor_vt) != 7:
                    return "⚠ A placa deve ter 7 caracteres (ex:  *ABC1234* ). Tente novamente."
            return await ParametrosService._alterar_veiculo_texto(
                motorista_id, tenant_id,
                coluna=campo_vt,
                valor=valor_vt[:150],
                label=("Modelo do Veículo" if campo_vt == "modelo" else "Placa do Veículo"),
            )

        # ── Comando !alterar combustivel ──────────────────────────────────
        # Altera tipo_combustivel + flags is_flex/is_hibrido/is_eletrico + JSONB.meta
        match_comb = re.match(
            r"^!alterar\s+combustivel\s+(gasolina|etanol|flex|hibrido|eletrico|gnv)$",
            texto, re.IGNORECASE,
        )
        if match_comb:
            return await ParametrosService._alterar_combustivel(
                motorista_id, tenant_id, match_comb.group(1).lower()
            )

        # ── Comando !alterar escala ───────────────────────────────────────
        # Aceita texto descritivo da escala semanal com inferência de dias/semana.
        # Ex: !alterar escala seg a sab   → 6 dias, "Seg a Sáb (6 dias)"
        #     !alterar escala seg a sex   → 5 dias, "Seg a Sex (5 dias)"
        #     !alterar escala ter a dom   → 6 dias, "Ter a Dom (6 dias)"
        #     !alterar escala 5           → 5 dias, mantém escala textual existente
        match_escala = re.match(r"^!alterar\s+escala\s+(.+)$", texto, re.IGNORECASE)
        if match_escala:
            return await ParametrosService._alterar_escala(motorista_id, tenant_id, match_escala.group(1).strip())

        # ── Comando !alterar ─────────────────────────────────────────────
        match = ParametrosService._RE_ALTERAR.match(texto)
        if not match:
            # Não é um comando '!' reconhecido — devolve None para o orquestrador
            return None

        param_nome = match.group(1).strip().lower()
        valor_raw  = match.group(2).strip()

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
                        "ORDER BY selecionado DESC, created_at DESC LIMIT 1;",
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
                    # Validação de range para dias_trabalho_semana
                    if coluna == "dias_trabalho_semana":
                        if not (1 <= int(valor_final) <= 7):
                            return f"⚠ Dias por semana deve ser entre 1 e 7. Informado: {valor_final}"
                    await conn.execute(
                        f"UPDATE public.veiculos SET {coluna} = $1 WHERE motorista_id = $2::uuid AND ativo = TRUE AND selecionado = TRUE;",
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
    async def _alterar_perfil_texto(
        motorista_id: str, tenant_id: str, campo: str, valor: str, label: str
    ) -> str:
        """Atualiza um campo de texto do perfil do motorista (nome ou nome_social)."""
        _COLUNAS_SEGURAS = {"nome", "nome_social"}
        if campo not in _COLUNAS_SEGURAS:
            return "⚠ Campo não permitido."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                await conn.execute(
                    f"UPDATE public.motoristas SET {campo} = $1 WHERE id = $2::uuid;",
                    valor, motorista_id,
                )
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")
            await ParametrosService._registrar_auditoria(tenant_id, motorista_id, campo, campo, valor)
            return (
                f"✅  *{label}*  atualizado!\n"
                f"• Novo valor:  *{valor}*\n"
                + ("_Seu nome de chamada foi alterado — aparecerá assim no próximo Raio-X._"
                   if campo == "nome_social" else
                   "_Nome cadastrado atualizado com sucesso._")
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao alterar {campo} (motorista={motorista_id}): {exc}")
            return "❌ Erro interno. Tente novamente."

    @staticmethod
    async def _alterar_veiculo_texto(
        motorista_id: str, tenant_id: str, coluna: str, valor: str, label: str
    ) -> str:
        """Atualiza um campo de texto do veículo ativo (modelo ou placa)."""
        _COLUNAS_SEGURAS = {"modelo", "placa"}
        if coluna not in _COLUNAS_SEGURAS:
            return "⚠ Campo não permitido."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # placa tem constraint UNIQUE — captura violação amigavelmente
                try:
                    await conn.execute(
                        f"UPDATE public.veiculos SET {coluna} = $1 "
                        f"WHERE motorista_id = $2::uuid AND ativo = TRUE AND selecionado = TRUE;",
                        valor, motorista_id,
                    )
                except Exception as db_exc:
                    if "unique" in str(db_exc).lower() or "duplicate" in str(db_exc).lower():
                        return f"⚠ A placa  *{valor}*  já está cadastrada em outro veículo."
                    raise
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")
            await ParametrosService._registrar_auditoria(tenant_id, motorista_id, coluna, coluna, valor)
            return f"✅  *{label}*  atualizado!\n• Novo valor:  *{valor}*"
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao alterar {coluna} (motorista={motorista_id}): {exc}")
            return "❌ Erro interno. Tente novamente."

    @staticmethod
    async def _alterar_combustivel(motorista_id: str, tenant_id: str, tipo: str) -> str:
        """Atualiza tipo_combustivel + flags booleanas + JSONB.meta no veículo ativo.

        Mantém todos os dados do estoque intactos — apenas atualiza os metadados
        de motorização para que os próximos cálculos de Power Split usem as flags corretas.
        """
        _flags = {
            "gasolina": {"is_flex": False, "is_hibrido": False, "is_eletrico": False},
            "etanol":   {"is_flex": False, "is_hibrido": False, "is_eletrico": False},
            "flex":     {"is_flex": True,  "is_hibrido": False, "is_eletrico": False},
            "hibrido":  {"is_flex": False, "is_hibrido": True,  "is_eletrico": False},
            "eletrico": {"is_flex": False, "is_hibrido": False, "is_eletrico": True},
            "gnv":      {"is_flex": False, "is_hibrido": False, "is_eletrico": False},
        }
        flags = _flags.get(tipo)
        if flags is None:
            return f"⚠ Combustível  *{tipo}*  não reconhecido."

        _LABEL = {"gasolina": "Gasolina", "etanol": "Etanol", "flex": "Flex (Gasolina/Etanol)",
                  "hibrido": "Híbrido", "eletrico": "Elétrico", "gnv": "GNV"}
        try:
            import json as _json_local
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, estoque_financeiro FROM public.veiculos "
                    "WHERE motorista_id = $1::uuid AND ativo = TRUE AND selecionado = TRUE FOR UPDATE;",
                    motorista_id,
                )
                if not row:
                    return "⚠ Nenhum veículo ativo localizado."
                raw = row["estoque_financeiro"]
                estoque = _json_local.loads(raw) if isinstance(raw, str) else (raw or {})
                from services.turno_service import TurnoService
                estoque = TurnoService._garantir_estrutura_estoque(estoque)
                # Atualiza as flags de motorização no JSONB.meta
                estoque["meta"]["tipo_veiculo"]  = tipo
                estoque["meta"]["is_flex"]        = flags["is_flex"]
                estoque["meta"]["is_hibrido"]     = flags["is_hibrido"]
                estoque["meta"]["is_eletrico"]    = flags["is_eletrico"]
                await conn.execute(
                    """
                    UPDATE public.veiculos
                    SET tipo_combustivel = $1, is_flex = $2, is_hibrido = $3, is_eletrico = $4,
                        estoque_financeiro = $5::jsonb
                    WHERE id = $6::uuid;
                    """,
                    tipo, flags["is_flex"], flags["is_hibrido"], flags["is_eletrico"],
                    _json_local.dumps(estoque), str(row["id"]),
                )
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, "combustivel", "tipo_combustivel", tipo
            )
            aviso = (
                "\n\n⚠️  _Lembre de conferir os rendimentos com  *!alterar km gasolina*  e  "
                "*!alterar km etanol*  se necessário._"
                if tipo == "flex" else ""
            )
            return (
                f"✅  *Combustível*  atualizado para  *{_LABEL[tipo]}* !\n"
                f"_Os próximos abastecimentos e cálculos de DRE usarão as novas configurações._{aviso}"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao alterar combustivel (motorista={motorista_id}): {exc}")
            return "❌ Erro interno. Tente novamente."

    @staticmethod
    async def _alterar_escala(motorista_id: str, tenant_id: str, valor_bruto: str) -> str:
        """Atualiza a escala semanal de trabalho (dias_trabalho_semana + escala_trabalho).

        Aceita dois formatos:
          • Número puro:  "6"  → 6 dias/semana, mantém texto da escala existente
          • Texto de escala:  "seg a sab" | "seg a sex" | "ter a dom"
            → infere automaticamente o número de dias e gera texto formatado

        Escala → dias mapeados pelo intervalo ISO (1=Seg…7=Dom):
          Seg a Dom = 7 | Seg a Sab = 6 | Seg a Sex = 5 | Ter a Dom = 6
          Qua a Dom = 5 | Seg a Qui = 4 | etc.
        """
        _ABREV = {
            "seg": 1, "segunda": 1,
            "ter": 2, "terca": 2, "terça": 2,
            "qua": 3, "quarta": 3,
            "qui": 4, "quinta": 4,
            "sex": 5, "sexta": 5,
            "sab": 6, "sáb": 6, "sabado": 6, "sábado": 6,
            "dom": 7, "domingo": 7,
        }
        _NOME_CURTO = {1:"Seg", 2:"Ter", 3:"Qua", 4:"Qui", 5:"Sex", 6:"Sáb", 7:"Dom"}
        _NOME_LONGO = {1:"segunda",2:"terça",3:"quarta",4:"quinta",5:"sexta",6:"sábado",7:"domingo"}

        val_norm = valor_bruto.lower().strip()
        # Remove acentos simples para matching
        val_norm = (val_norm.replace("á","a").replace("â","a").replace("ã","a")
                            .replace("é","e").replace("ê","e")
                            .replace("ó","o").replace("ô","o")
                            .replace("ú","u").replace("í","i"))

        dias_calculados: int | None = None
        texto_escala: str | None = None

        # Tenta parse numérico puro
        if re.fullmatch(r'\d+', val_norm):
            n = int(val_norm)
            if not (1 <= n <= 7):
                return f"⚠ Valor inválido:  *{n}*  dias. Use um número entre 1 e 7."
            dias_calculados = n
            # texto_escala = None → mantém o existente no banco

        else:
            # Tenta parse de intervalo "DIA_INICIO a DIA_FIM"
            match_intervalo = re.match(
                r'^([a-z]+)\s+a\s+([a-z]+)$', val_norm
            )
            if match_intervalo:
                ini_str, fim_str = match_intervalo.group(1), match_intervalo.group(2)
                ini_iso = _ABREV.get(ini_str)
                fim_iso = _ABREV.get(fim_str)
                if ini_iso is None or fim_iso is None:
                    return (
                        f"⚠ Não reconheci  *{ini_str}*  ou  *{fim_str}*  como dia da semana.\n"
                        f"Use abreviações: Seg, Ter, Qua, Qui, Sex, Sáb, Dom."
                    )
                # Dias inclusivos, com wrap de semana (ex: Qua a Ter = 7)
                if fim_iso >= ini_iso:
                    dias_calculados = fim_iso - ini_iso + 1
                else:
                    dias_calculados = (7 - ini_iso + 1) + fim_iso
                ini_label = _NOME_CURTO[ini_iso]
                fim_label = _NOME_CURTO[fim_iso]
                texto_escala = f"{ini_label} a {fim_label} ({dias_calculados} dias)"
            else:
                return (
                    f"⚠ Não entendi  *{valor_bruto}* .\n\n"
                    f"Use um dos formatos:\n"
                    f"  *!alterar escala 6*          → 6 dias por semana\n"
                    f"  *!alterar escala seg a sab*  → segunda a sábado (6 dias)\n"
                    f"  *!alterar escala seg a sex*  → segunda a sexta (5 dias)\n"
                    f"  *!alterar escala ter a dom*  → terça a domingo (6 dias)"
                )

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                if texto_escala is not None:
                    # Atualiza ambos: dias e texto descritivo
                    await conn.execute(
                        "UPDATE public.veiculos SET dias_trabalho_semana = $1, escala_trabalho = $2 "
                        "WHERE motorista_id = $3::uuid AND ativo = TRUE AND selecionado = TRUE;",
                        dias_calculados, texto_escala, motorista_id,
                    )
                else:
                    # Só atualiza o número; preserva o texto existente
                    await conn.execute(
                        "UPDATE public.veiculos SET dias_trabalho_semana = $1 "
                        "WHERE motorista_id = $2::uuid AND ativo = TRUE AND selecionado = TRUE;",
                        dias_calculados, motorista_id,
                    )

            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, "escala", "dias_trabalho_semana", dias_calculados
            )

            escala_fmt = texto_escala or f"{dias_calculados} dias/semana"
            # Recalcula o custo diário estimado para mostrar o impacto imediato
            # Lê o aluguel atual do banco para não depender de cache
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                v_row = await conn.fetchrow(
                    "SELECT custo_aluguel_semanal FROM public.veiculos "
                    "WHERE motorista_id = $1::uuid AND ativo = TRUE AND selecionado = TRUE;",
                    motorista_id,
                )
            aluguel_sem = float(v_row["custo_aluguel_semanal"] or 0) if v_row else 0
            aluguel_dia = aluguel_sem / dias_calculados if dias_calculados > 0 else 0

            return (
                f"✅  *Escala atualizada!*\n"
                f"• Nova escala:  *{escala_fmt}*\n"
                f"• Dias por semana:  *{dias_calculados}*\n"
                + (f"• Custo diário recalculado:  *R$ {aluguel_dia:.2f}*  (R$ {aluguel_sem:.2f} ÷ {dias_calculados}d)\n" if aluguel_sem > 0 else "")
                + f"\n_O próximo DRE já usará o novo valor diário do aluguel._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao alterar escala (motorista={motorista_id}): {exc}")
            return "❌ Erro interno ao salvar a escala. Tente novamente."

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
                    "ORDER BY selecionado DESC, created_at DESC LIMIT 1 FOR UPDATE;",
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

                # Sincroniza coluna plana capacidade_bateria quando o motorista altera
                # capacidade_bateria_kwh no JSONB — evita divergência entre as duas fontes.
                if coluna == "capacidade_bateria_kwh":
                    await conn.execute(
                        "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb, "
                        "capacidade_bateria = $2 WHERE id = $3::uuid;",
                        _json.dumps(estoque), valor_float, veiculo_id,
                    )
                else:
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
                    SELECT nome, valor_mensal, dias_trabalho_previstos,
                           valor_pro_rata_diario, dias_vencimento,
                           recorrencia_tipo, dias_semana,
                           parcelas_totais, parcelas_pagas, data_inicio
                    FROM public.despesas_fixas_mensais
                    WHERE motorista_id = $1::uuid AND ativo = TRUE
                    ORDER BY recorrencia_tipo, dias_vencimento[1], valor_mensal DESC;
                    """,
                    motorista_id,
                )
            if not rows:
                return (
                    "📌  *Nenhuma despesa fixa cadastrada ainda.*\n\n"
                    "Para adicionar, envie:\n"
                    "  *!adicionar despesa <nome> <R$/mês> [dias] [venc1] [venc2...]*\n"
                    "_Ex: !adicionar despesa seguro 180 26 5_\n"
                    "_Ex: !adicionar despesa cartao 800 26 5 20_  _(2 vencimentos)_\n\n"
                    "Para despesas semanais:\n"
                    "  *!adicionar despesa semanal <nome> <R$/mês> [1-7]*\n"
                    "_Ex: !adicionar despesa semanal diaria 80 5_  _(toda sexta)_"
                )
            from datetime import date as _date, timedelta as _timedelta
            hoje       = _date.today()
            hoje_dia   = hoje.day
            amanha_dia = (hoje + _timedelta(days=1)).day
            hoje_iso   = hoje.isoweekday()          # 1=Seg … 7=Dom
            amanha_iso = (hoje + _timedelta(days=1)).isoweekday()

            _DIAS_SEMANA_LABEL = {1: "Seg", 2: "Ter", 3: "Qua", 4: "Qui", 5: "Sex", 6: "Sáb", 7: "Dom"}
            _DIAS_SEMANA_FULL  = {1: "Segunda", 2: "Terça", 3: "Quarta", 4: "Quinta",
                                   5: "Sexta", 6: "Sábado", 7: "Domingo"}

            total_pro_rata = sum(float(r["valor_pro_rata_diario"]) for r in rows)
            linhas = ["📌  *Despesas Fixas Ativas:*\n"]
            for r in rows:
                nome      = r["nome"]
                mensal    = float(r["valor_mensal"])
                diario    = float(r["valor_pro_rata_diario"])
                rec_tipo  = r.get("recorrencia_tipo") or "mensal"
                alertas   = []

                if rec_tipo == "semanal":
                    dias_s   = list(r["dias_semana"] or [1])
                    qtd_v    = len(dias_s)
                    nomes_ds = [_DIAS_SEMANA_FULL.get(d, str(d)) for d in dias_s]
                    venc_str = f"📅 toda  *{' e '.join(nomes_ds)}*  (semanal)"
                    if hoje_iso in dias_s:
                        alertas.append("⚠️ *VENCE HOJE!*")
                    elif amanha_iso in dias_s:
                        alertas.append("⏰ _vence amanhã_")
                    parcela = mensal / qtd_v if qtd_v > 1 else mensal
                    extra = f"  (≈ R$ {parcela:.2f}/semana)" if qtd_v == 1 else f"  ({qtd_v}× /sem · ≈ R$ {parcela:.2f} cada)"
                else:
                    dias_venc = list(r["dias_vencimento"] or [1])
                    qtd_v     = len(dias_venc)
                    if hoje_dia in dias_venc:
                        alertas.append("⚠️ *VENCE HOJE!*")
                    if amanha_dia in dias_venc:
                        alertas.append("⏰ _vence amanhã_")
                    if qtd_v == 1:
                        venc_str = f"📅 vence dia  *{dias_venc[0]}*"
                    elif qtd_v <= 6:
                        venc_str = "📅 vence dias  " + " e ".join(f"*{d}*" for d in dias_venc)
                    else:
                        parcela_m = mensal / qtd_v
                        venc_str = f"📅 {qtd_v}× (dias *{dias_venc[0]}*–*{dias_venc[-1]}* · ≈ R$ {parcela_m:.2f}/parcela)"
                    extra = ""

                alerta = ("  " + "  ".join(alertas)) if alertas else ""

                # Tag de ciclo de vida: Parcela X de N / Única / nada para perpétuas
                _pt = r.get("parcelas_totais")
                _pp = int(r.get("parcelas_pagas") or 0)
                if _pt is not None:
                    _restam = _pt - _pp
                    if _pt == 1:
                        tag_ciclo = "  ⚡ _única_"
                    elif _restam == 1:
                        tag_ciclo = f"  🔚 _última parcela ({_pp+1}/{_pt})_"
                    else:
                        tag_ciclo = f"  📋 _{_pp+1}/{_pt} parcelas_"
                else:
                    tag_ciclo = ""

                linhas.append(
                    f"• {nome}:  *R$ {mensal:.2f}/mês*"
                    f"  (≈ R$ {diario:.2f}/dia)  {venc_str}{extra}{alerta}{tag_ciclo}"
                )
            linhas.append(f"\n*Total pro-rata diário: R$ {total_pro_rata:.2f}*")
            linhas.append(
                "\n_Para remover, envie:  *!remover despesa <nome>*_\n"
                "_Para adicionar mensal:  *!adicionar despesa <nome> <R$/mês> [dias] [venc1...]*_\n"
                "_Para adicionar semanal: *!adicionar despesa semanal <nome> <R$/mês> [1-7]*_"
            )
            return "\n".join(linhas)
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao listar despesas fixas (motorista={motorista_id}): {exc}")
            return "❌ Erro ao buscar despesas fixas. Tente novamente em instantes."
    @staticmethod
    async def _obter_ou_criar_caixa(
        conn, motorista_id: str, nome_caixa: str,
        meta_valor: Optional[Decimal] = None,
    ) -> str:
        """Retorna o UUID da caixa com o nome informado, criando-a se não existir.

        Se `meta_valor` for fornecida:
          - Na criação: define a meta imediatamente.
          - Na atualização: preenche `meta_valor` apenas se ainda estiver NULL
            (preserva meta que o motorista tiver definido manualmente).
        """
        row = await conn.fetchrow(
            "SELECT id, meta_valor FROM public.caixas_provisao "
            "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
            motorista_id, nome_caixa,
        )
        if row:
            # Caixa já existe — preenche meta somente se ainda não tiver nenhuma
            if meta_valor is not None and row["meta_valor"] is None:
                await conn.execute(
                    "UPDATE public.caixas_provisao SET meta_valor = $1 WHERE id = $2::uuid;",
                    meta_valor, str(row["id"]),
                )
            return str(row["id"])
        new_id = await conn.fetchval(
            """
            INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual, meta_valor)
            VALUES ($1::uuid, $2, 0.00, $3)
            ON CONFLICT (motorista_id, nome_caixa) DO UPDATE
                SET meta_valor = COALESCE(caixas_provisao.meta_valor, EXCLUDED.meta_valor)
            RETURNING id;
            """,
            motorista_id, nome_caixa, meta_valor,
        )
        return str(new_id)

    @staticmethod
    async def sincronizar_despesa_contrato(
        motorista_id: str,
        tenant_id: str,
        locadora: str,
        aluguel_semanal: float,
        dias_uteis: int = 26,
        dia_vencimento: int = 1,
        dias_vencimento: list[int] | None = None,
        recorrencia_tipo: str = "mensal",
        dias_semana: list[int] | None = None,
    ) -> None:
        """Cria ou atualiza automaticamente a despesa fixa e a caixinha do contrato veicular.

        Parâmetros de vencimento (mutuamente exclusivos):
          • dia_vencimento  — legado: aceita um único dia do mês (compat. retroativa)
          • dias_vencimento — array de dias do mês (ex: [5, 20]); sobrepõe dia_vencimento
          • recorrencia_tipo — 'mensal' (default) | 'semanal'
          • dias_semana      — array ISO weekday (ex: [5] = toda sexta), só se semanal

        Quando nenhum argumento de vencimento é fornecido, usa dia_vencimento=1.
        """
        # Resolve o array de dias do mês a persistir
        if dias_vencimento is not None:
            _dias_venc = sorted(set(d for d in dias_vencimento if 1 <= d <= 31)) or [1]
        else:
            _dias_venc = [dia_vencimento] if 1 <= dia_vencimento <= 31 else [1]

        # Para recorrência semanal, dias_semana deve estar preenchido
        _recorrencia = recorrencia_tipo if recorrencia_tipo in ("mensal", "semanal") else "mensal"
        _dias_semana = (
            sorted(set(d for d in dias_semana if 1 <= d <= 7))
            if _recorrencia == "semanal" and dias_semana
            else None
        )
        # Se semanal sem dias_semana válido, degrade para mensal
        if _recorrencia == "semanal" and not _dias_semana:
            _recorrencia = "mensal"

        try:
            locadora_lower = locadora.strip().lower()
            is_proprio = locadora_lower in ("proprietario", "quitado", "financiado")

            # Nome canônico da despesa de contrato
            if locadora_lower == "financiado":
                nome_despesa = "Parcela Financiamento"
            elif locadora_lower in ("proprietario", "quitado"):
                nome_despesa = "Custo Veículo Próprio"
            elif locadora_lower in ("alugado", "aluguel"):
                # Genérico: motorista informou o tipo em vez do nome da locadora
                nome_despesa = "Aluguel Veículo"
            else:
                # Locadora real: "Aluguel Zarp", "Aluguel Movida", etc.
                nome_despesa = f"Aluguel {locadora.strip().title()}"

            # Pro-rata diário:
            # • Mensal: valor_mensal_calculado / dias_uteis (pro-rata por dia de trabalho)
            # • Semanal: aluguel_semanal / 7 (custo por dia de calendário, base 28d/4sem)
            #   — o denominador é sempre 28 para garantir que em 4 semanas a caixinha
            #   acumule exatamente o valor semanal × 4 necessário.
            if _recorrencia == "semanal":
                # valor_mensal = aluguel semanal × 4 semanas (mês padrão de 4 semanas)
                valor_mensal  = (Decimal(str(aluguel_semanal)) * Decimal("4")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                # pro-rata diário = valor semanal / 7 dias
                _dias_pro_rata = 28  # denominador fixo para semanal (4 sem × 7 dias)
            else:
                # Mensal: pro-rata proporcional aos dias úteis do motorista
                valor_mensal  = (
                    Decimal(str(aluguel_semanal)) * Decimal(str(dias_uteis)) / Decimal("7")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                _dias_pro_rata = dias_uteis

            # Meta da caixinha = valor que precisa acumular por ciclo de pagamento
            # • Mensal: valor_mensal (acumula durante o mês para pagar no dia X)
            # • Semanal: aluguel_semanal (acumula durante a semana para pagar na terça/etc.)
            meta_caixa = (
                Decimal(str(aluguel_semanal)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if _recorrencia == "semanal"
                else valor_mensal
            )

            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Desativa despesas de contrato anteriores com nome diferente
                #    (evita duplicatas ao trocar de "Aluguel Zarp" para "Aluguel Movida")
                await conn.execute(
                    """
                    UPDATE public.despesas_fixas_mensais
                    SET ativo = FALSE
                    WHERE motorista_id = $1::uuid
                      AND ativo = TRUE
                      AND lower(nome) LIKE ANY(ARRAY['aluguel %', 'parcela financiamento', 'custo veículo próprio', 'custo veiculo proprio'])
                      AND lower(nome) != lower($2);
                    """,
                    motorista_id, nome_despesa,
                )

                # 2. Cria/atualiza a caixinha do contrato
                # Meta = valor do ciclo de pagamento (semanal: R$ 250 | mensal: R$ 928,57)
                caixa_id = await ParametrosService._obter_ou_criar_caixa(
                    conn, motorista_id, nome_despesa, meta_valor=meta_caixa
                )

                # 3. Upsert da despesa fixa de contrato
                existing = await conn.fetchrow(
                    "SELECT id FROM public.despesas_fixas_mensais "
                    "WHERE motorista_id = $1::uuid AND lower(nome) = lower($2);",
                    motorista_id, nome_despesa,
                )
                if existing:
                    await conn.execute(
                        "UPDATE public.despesas_fixas_mensais "
                        "SET valor_mensal = $1, dias_trabalho_previstos = $2, ativo = TRUE, "
                        "    caixa_id = $3::uuid, dias_vencimento = $4::integer[], "
                        "    recorrencia_tipo = $5, dias_semana = $6::integer[] "
                        "WHERE id = $7::uuid;",
                        valor_mensal, _dias_pro_rata, caixa_id,
                        _dias_venc, _recorrencia, _dias_semana, str(existing["id"]),
                    )
                    # Sincroniza meta da caixa com o ciclo de pagamento correto
                    await conn.execute(
                        "UPDATE public.caixas_provisao SET meta_valor = $1 WHERE id = $2::uuid;",
                        meta_caixa, caixa_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO public.despesas_fixas_mensais
                            (motorista_id, nome, valor_mensal, dias_trabalho_previstos,
                             dias_vencimento, recorrencia_tipo, dias_semana, caixa_id)
                        VALUES ($1::uuid, $2, $3, $4, $5::integer[], $6, $7::integer[], $8::uuid);
                        """,
                        motorista_id, nome_despesa, valor_mensal, _dias_pro_rata,
                        _dias_venc, _recorrencia, _dias_semana, caixa_id,
                    )

                # 4. Para carro alugado: remove caixa "Amortização de IPVA/Seguro" se estiver
                #    vazia (IPVA não é responsabilidade do motorista em carro alugado).
                if not is_proprio:
                    await conn.execute(
                        """
                        DELETE FROM public.caixas_provisao
                        WHERE motorista_id = $1::uuid
                          AND lower(nome_caixa) = 'amortização de ipva/seguro'
                          AND saldo_atual = 0;
                        """,
                        motorista_id,
                    )

                # 5. Para carro próprio/financiado: garante que a caixa de IPVA/Seguro existe
                if is_proprio:
                    await conn.execute(
                        """
                        INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual)
                        VALUES ($1::uuid, 'Amortização de IPVA/Seguro', 0.00)
                        ON CONFLICT (motorista_id, nome_caixa) DO NOTHING;
                        """,
                        motorista_id,
                    )

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id,
                f"contrato_{nome_despesa}", "valor_mensal", valor_mensal,
            )
            logger.info(
                f"[ParametrosService] Despesa de contrato sincronizada: motorista={motorista_id} "
                f"nome={nome_despesa!r} valor_mensal=R${float(valor_mensal):.2f} dias={dias_uteis}"
            )
        except Exception as exc:
            logger.error(
                f"[ParametrosService] Erro ao sincronizar despesa de contrato "
                f"(motorista={motorista_id}): {exc}"
            )

    @staticmethod
    async def _adicionar_despesa_semanal(
        motorista_id: str, tenant_id: str, nome: str,
        valor_mensal: Decimal, dia_iso: int,
    ) -> str:
        """Cadastra uma despesa com recorrência semanal (vence todo dia_iso da semana).

        `dia_iso` segue ISO-8601: 1=Segunda … 7=Domingo.
        O valor_mensal é o total mensal estimado; cada parcela semanal = valor_mensal / 4.
        O pro-rata diário usa 28 dias como denominador (4 semanas × 7 dias),
        garantindo paridade com o calendário semanal de 4 vencimentos por mês.
        """
        _DIAS_FULL = {1: "Segunda-feira", 2: "Terça-feira", 3: "Quarta-feira",
                      4: "Quinta-feira", 5: "Sexta-feira", 6: "Sábado", 7: "Domingo"}
        if valor_mensal <= 0:
            return "⚠ O valor mensal deve ser maior que zero."
        if not (1 <= dia_iso <= 7):
            return "⚠ Dia da semana inválido. Use 1=Segunda … 7=Domingo."

        # Pro-rata diário para despesa semanal: valor_mensal / 28 dias
        # (equivale a 4 semanas por mês — denominador fixo para consistência)
        dias_pro_rata = 28

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                caixa_id = await ParametrosService._obter_ou_criar_caixa(
                    conn, motorista_id, nome, meta_valor=valor_mensal
                )
                existing = await conn.fetchrow(
                    "SELECT id FROM public.despesas_fixas_mensais "
                    "WHERE motorista_id = $1::uuid AND lower(nome) = lower($2);",
                    motorista_id, nome,
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE public.despesas_fixas_mensais
                        SET valor_mensal = $1, dias_trabalho_previstos = $2, ativo = TRUE,
                            caixa_id = $3::uuid, recorrencia_tipo = 'semanal',
                            dias_semana = $4::integer[], dias_vencimento = ARRAY[1]::integer[]
                        WHERE id = $5::uuid;
                        """,
                        valor_mensal, dias_pro_rata, caixa_id,
                        [dia_iso], str(existing["id"]),
                    )
                    await conn.execute(
                        "UPDATE public.caixas_provisao SET meta_valor = $1 WHERE id = $2::uuid;",
                        valor_mensal, caixa_id,
                    )
                    acao = "reativada e atualizada"
                else:
                    await conn.execute(
                        """
                        INSERT INTO public.despesas_fixas_mensais
                            (motorista_id, nome, valor_mensal, dias_trabalho_previstos,
                             dias_vencimento, recorrencia_tipo, dias_semana, caixa_id)
                        VALUES ($1::uuid, $2, $3, $4, ARRAY[1]::integer[], 'semanal', $5::integer[], $6::uuid);
                        """,
                        motorista_id, nome, valor_mensal, dias_pro_rata,
                        [dia_iso], caixa_id,
                    )
                    acao = "adicionada"

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"despesa_semanal_{nome}", "valor_mensal", valor_mensal
            )
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")

            parcela_sem = float(valor_mensal / Decimal("4"))
            pro_rata    = float(valor_mensal / Decimal(str(dias_pro_rata)))
            dia_label   = _DIAS_FULL.get(dia_iso, str(dia_iso))
            return (
                f"✅  *Despesa semanal {acao}!*\n"
                f"• Nome:  *{nome}*\n"
                f"• Valor mensal:  *R$ {float(valor_mensal):.2f}*  (≈ R$ {parcela_sem:.2f}/semana)\n"
                f"• Vencimento:  *toda  {dia_label}*\n"
                f"• Pro-rata diário:  *R$ {pro_rata:.2f}*  (base: 28 dias)\n"
                f"• Caixinha vinculada:  *{nome}*  _(aportes automáticos a cada fechamento)_\n\n"
                f"_Esse custo será deduzido automaticamente em cada fechamento de turno._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao adicionar despesa semanal (motorista={motorista_id}): {exc}")
            return "❌ Erro ao salvar a despesa semanal. Verifique os dados e tente novamente."

    @staticmethod
    async def _adicionar_despesa_fixa(
        motorista_id: str, tenant_id: str, nome: str, valor_mensal: Decimal,
        dias: Optional[int],
        dias_vencimento: list[int],
        parcelas_totais: Optional[int] = None,
        data_inicio: Optional["date"] = None,
        valor_total: Optional[Decimal] = None,
        frequencia_dias: Optional[int] = None,
    ) -> str:
        """Insere ou reativa uma despesa fixa mensal e vincula à caixinha de provisão correspondente.

        `dias` é o número de dias úteis usado como denominador do pro-rata diário.
        Se None, é preenchido automaticamente com dias_uteis_mes do motorista.

        `dias_vencimento` é a lista de dias do mês em que a despesa vence.

        `parcelas_totais` — None = perpétua | 1 = única | N > 1 = parcelamento em N vezes.
        `data_inicio` — None = vigente desde já | date = começa a cobrar a partir desta data.
        `valor_total` — Valor global do contrato para absorção de centavos na última parcela.
        `frequencia_dias` — Intervalo em dias (ex: 15 para quinzenal, 30 para mensal).
        """
        if valor_mensal <= 0:
            return "⚠ O valor mensal deve ser maior que zero."
        if dias is not None and not (1 <= dias <= 31):
            return "⚠ O número de dias úteis deve estar entre 1 e 31."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # Busca dias_uteis_mes do motorista para validação e fallback
                row_m = await conn.fetchrow(
                    "SELECT dias_uteis_mes FROM public.motoristas WHERE id = $1::uuid;",
                    motorista_id,
                )
                dias_uteis_mes: int = int(row_m["dias_uteis_mes"]) if row_m else 26

                aviso_dias = ""
                if dias is None:
                    # Sem argumento explícito: usa o padrão do motorista
                    dias = dias_uteis_mes
                elif dias != dias_uteis_mes:
                    # Informa divergência mas respeita a escolha do usuário
                    pro_correto = float(valor_mensal / Decimal(str(dias_uteis_mes)))
                    aviso_dias = (
                        f"\n\n⚠  _Seus dias úteis cadastrados são  *{dias_uteis_mes}*  mas você informou  "
                        f"*{dias}*  — o pro-rata fica  *R$ {pro_correto:.2f}/dia*  com {dias_uteis_mes} dias._\n"
                        f"_Para corrigir: `!adicionar despesa {nome} {float(valor_mensal):.0f} {dias_uteis_mes}`_"
                    )

                # Garante que existe uma caixa de provisão com o mesmo nome da despesa.
                # meta = valor_mensal: define o teto da caixa igual ao custo mensal.
                caixa_id = await ParametrosService._obter_ou_criar_caixa(conn, motorista_id, nome, meta_valor=valor_mensal)
                # Verifica se já existe (mesmo nome, case-insensitive) para reativar
                existing = await conn.fetchrow(
                    "SELECT id, ativo FROM public.despesas_fixas_mensais "
                    "WHERE motorista_id = $1::uuid AND lower(nome) = lower($2);",
                    motorista_id, nome,
                )
                if existing:
                    await conn.execute(
                        "UPDATE public.despesas_fixas_mensais "
                        "SET valor_mensal = $1, dias_trabalho_previstos = $2, ativo = TRUE, "
                        "    caixa_id = $3::uuid, dias_vencimento = $4, "
                        "    parcelas_totais = $5, parcelas_pagas = 0, data_inicio = $6, "
                        "    valor_total = $7, frequencia_dias = $8 "
                        "WHERE id = $9::uuid;",
                        valor_mensal, dias, caixa_id, dias_vencimento,
                        parcelas_totais, data_inicio, valor_total, frequencia_dias,
                        str(existing["id"]),
                    )
                    # Sincroniza meta da caixa com o novo valor_mensal
                    await conn.execute(
                        "UPDATE public.caixas_provisao SET meta_valor = $1 WHERE id = $2::uuid;",
                        valor_mensal, caixa_id,
                    )
                    acao = "reativada e atualizada"
                else:
                    await conn.execute(
                        """
                        INSERT INTO public.despesas_fixas_mensais
                            (motorista_id, nome, valor_mensal, dias_trabalho_previstos,
                             dias_vencimento, caixa_id, parcelas_totais, data_inicio,
                             valor_total, frequencia_dias)
                        VALUES ($1::uuid, $2, $3, $4, $5::integer[], $6::uuid, $7, $8, $9, $10);
                        """,
                        motorista_id, nome, valor_mensal, dias, dias_vencimento,
                        caixa_id, parcelas_totais, data_inicio,
                        valor_total, frequencia_dias,
                    )
                    acao = "adicionada"

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"despesa_fixa_{nome}", "valor_mensal", valor_mensal
            )
            pro_rata = float(valor_mensal / Decimal(str(dias)))
            venc_str = " e ".join(f"dia *{d}*" for d in dias_vencimento)

            # Linha descritiva de parcelamento
            if parcelas_totais is None:
                linha_parcelas = ""
                lbl_valor = "Valor mensal"
            elif parcelas_totais == 1:
                _data_str = f"  (a partir de *{data_inicio.strftime('%d/%m/%Y')}*)" if data_inicio else ""
                linha_parcelas = f"• Tipo:  *Despesa única*{_data_str}\n"
                lbl_valor = "Valor da despesa"
            else:
                _val_parc = (valor_total / Decimal(str(parcelas_totais))) if valor_total else (valor_mensal / Decimal(str(len(dias_vencimento))))
                _freq_txt = " quinzenais" if frequencia_dias == 15 else ""
                linha_parcelas = (
                    f"• Parcelamento:  *{parcelas_totais}×{_freq_txt} de R$ {float(_val_parc):.2f}* "
                    f"(Total: R$ {float(valor_total or valor_mensal):.2f})\n"
                )
                lbl_valor = "Provisão mensal (2 parcelas)" if frequencia_dias == 15 else "Valor por parcela"

            return (
                f"✅  *Despesa fixa {acao}!*\n"
                f"• Nome:  *{nome}*\n"
                f"• {lbl_valor}:  *R$ {float(valor_mensal):.2f}*\n"
                + linha_parcelas
                + f"• Pro-rata diário:  *R$ {pro_rata:.2f}*  (base: {dias} dias úteis)\n"
                f"• Vencimento:  *todo {venc_str}*\n"
                f"• Caixinha vinculada:  *{nome}*  _(aportes automáticos a cada fechamento)_\n\n"
                f"_Esse custo será deduzido automaticamente em cada fechamento de turno._"
                f"{aviso_dias}"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao adicionar despesa fixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao salvar a despesa fixa. Verifique os dados e tente novamente."

    @staticmethod
    def _barra_progresso_caixa(saldo: float, meta: float) -> str:
        """Barra ASCII de 10 blocos proporcional ao progresso saldo/meta."""
        pct = min(1.0, saldo / meta) if meta > 0 else 0.0
        cheios = int(pct * 10)
        return "█" * cheios + "░" * (10 - cheios)

    @staticmethod
    async def _listar_caixas(motorista_id: str) -> str:
        """Lista caixas de provisão com barra de progresso e projeção de conclusão."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                rows = await conn.fetch(
                    """
                    SELECT cp.id, cp.nome_caixa, cp.saldo_atual, cp.meta_valor,
                           COALESCE(SUM(dfm.valor_pro_rata_diario), 0) AS aporte_diario
                    FROM public.caixas_provisao cp
                    LEFT JOIN public.despesas_fixas_mensais dfm
                        ON dfm.caixa_id = cp.id AND dfm.ativo = TRUE
                    WHERE cp.motorista_id = $1::uuid
                    GROUP BY cp.id, cp.nome_caixa, cp.saldo_atual, cp.meta_valor
                    ORDER BY cp.nome_caixa;
                    """,
                    motorista_id,
                )
            if not rows:
                return (
                    "📦  *Nenhuma caixa de provisão cadastrada ainda.*\n\n"
                    "As caixas são criadas automaticamente quando você adiciona uma despesa fixa.\n"
                    "Ou crie manualmente:\n"
                    "  *!criar caixa pneu 500*   _(cria com meta de R$ 500)_\n"
                    "  *!criar caixa viagem*       _(cria sem meta — acumulação livre)_"
                )
            linhas = ["📦  *Caixas de Provisão:*\n"]
            total_saldo = 0.0
            for r in rows:
                saldo  = float(r["saldo_atual"])
                meta   = float(r["meta_valor"]) if r["meta_valor"] is not None else None
                aporte = float(r["aporte_diario"])
                total_saldo += saldo

                if meta is not None:
                    pct = min(100.0, saldo / meta * 100)
                    barra = ParametrosService._barra_progresso_caixa(saldo, meta)
                    if saldo >= meta:
                        status = f"✅ Meta atingida!  *R$ {saldo:.2f} / R$ {meta:.2f}*"
                        projecao_str = ""
                    else:
                        falta = meta - saldo
                        dias_para_meta = f"{falta / aporte:.0f} turnos" if aporte > 0 else "—"
                        status = f"*R$ {saldo:.2f} / R$ {meta:.2f}*  ({pct:.0f}%)"
                        projecao_str = (
                            f"\n  _Faltam R$ {falta:.2f}  ·  aprox. {dias_para_meta} para completar_"
                            if aporte > 0 else ""
                        )
                    linhas.append(
                        f"• {r['nome_caixa']}:  {status}\n"
                        f"  [{barra}]{projecao_str}"
                    )
                else:
                    # Sem meta — exibe saldo + aporte simples
                    aporte_str = f"  _(+R$ {aporte:.2f}/turno)_" if aporte > 0 else ""
                    linhas.append(f"• {r['nome_caixa']}:  *R$ {saldo:.2f}*{aporte_str}  _(sem meta definida)_")

            linhas.append(f"\n*Total reservado: R$ {total_saldo:.2f}*")
            linhas.append(
                "\n_!retirar caixa <nome> <valor>_   _!definir meta caixa <nome> <valor>_\n"
                "_!criar caixa <nome> <meta>_   _!excluir caixa <nome>_"
            )
            return "\n".join(linhas)
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao listar caixas (motorista={motorista_id}): {exc}")
            return "❌ Erro ao buscar caixas de provisão. Tente novamente em instantes."

    @staticmethod
    async def _criar_caixa(motorista_id: str, tenant_id: str, nome: str, meta: Optional[Decimal] = None) -> str:
        """Cria uma caixa de provisão. Se meta for informada, aportes param ao atingir o teto."""
        nome = nome.strip()[:60]
        if not nome:
            return "⚠ Informe um nome. Ex:  *!criar caixa pneu 500*"
        if meta is not None and meta <= 0:
            return "⚠ A meta deve ser maior que zero."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                existing = await conn.fetchrow(
                    "SELECT id, saldo_atual, meta_valor FROM public.caixas_provisao "
                    "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
                    motorista_id, nome,
                )
                if existing:
                    saldo = float(existing["saldo_atual"])
                    meta_atual = float(existing["meta_valor"]) if existing["meta_valor"] else None
                    meta_str = f"R$ {meta_atual:.2f}" if meta_atual else "sem meta"
                    return (
                        f"ℹ️  A caixa  *{nome}*  já existe.\n"
                        f"• Saldo:  *R$ {saldo:.2f}*  · Meta:  *{meta_str}*\n\n"
                        f"_Para alterar a meta:  *!definir meta caixa {nome} <valor>*_\n"
                        f"_Para sacar:  *!retirar caixa {nome} <valor>*_"
                    )
                await conn.execute(
                    "INSERT INTO public.caixas_provisao (motorista_id, nome_caixa, saldo_atual, meta_valor) "
                    "VALUES ($1::uuid, $2, 0.00, $3);",
                    motorista_id, nome, meta,
                )
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"criar_caixa_{nome}", "meta_valor", meta
            )
            meta_txt = f"Meta:  *R$ {float(meta):.2f}*  — aportes param ao atingir." if meta else "Sem meta — acumulação livre."
            return (
                f"✅  *Caixa {nome} criada!* \n"
                f"• {meta_txt}\n\n"
                f"_Para vincular a uma despesa fixa:_\n"
                f"  *!adicionar despesa {nome} <R$/mês> <dias>*"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao criar caixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao criar a caixa. Tente novamente."

    @staticmethod
    async def _definir_meta_caixa(motorista_id: str, tenant_id: str, nome: str, meta: Optional[Decimal]) -> str:
        """Define ou remove a meta (teto) de uma caixa existente."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, saldo_atual FROM public.caixas_provisao "
                    "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
                    motorista_id, nome,
                )
                if not row:
                    return (
                        f"⚠ Caixa  *{nome}*  não encontrada.\n"
                        f"Envie  *!caixas*  para ver a lista."
                    )
                await conn.execute(
                    "UPDATE public.caixas_provisao SET meta_valor = $1 WHERE id = $2::uuid;",
                    meta, str(row["id"]),
                )
            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"meta_caixa_{nome}", "meta_valor", meta
            )
            saldo = float(row["saldo_atual"])
            if meta is None:
                return (
                    f"✅  Meta da caixa  *{nome}*  removida.\n"
                    f"_Agora acumula livremente sem teto._"
                )
            falta = max(0.0, float(meta) - saldo)
            return (
                f"✅  Meta da caixa  *{nome}*  definida em  *R$ {float(meta):.2f}* !\n"
                f"• Saldo atual:  *R$ {saldo:.2f}*\n"
                f"• Falta:  *R$ {falta:.2f}*\n\n"
                f"_Os aportes automáticos param quando o saldo atingir a meta._"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao definir meta caixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao definir meta. Tente novamente."

    @staticmethod
    async def _retirar_caixa(motorista_id: str, tenant_id: str, nome: str, valor: Decimal) -> str:
        """Registra uma retirada (saque real) de uma caixa de provisão. Zera meta ao sacar tudo."""
        if valor <= 0:
            return "⚠ O valor da retirada deve ser maior que zero."
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, saldo_atual, meta_valor FROM public.caixas_provisao "
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

                # Verifica se há despesa parcelada vinculada a esta caixa para controle de concorrência
                dfm_row = await conn.fetchrow(
                    """
                    SELECT id, nome, valor_mensal, parcelas_totais, parcelas_pagas,
                           dias_vencimento, valor_total
                    FROM public.despesas_fixas_mensais
                    WHERE caixa_id = $1::uuid AND ativo = TRUE AND parcelas_totais IS NOT NULL;
                    """,
                    str(row["id"])
                )
                aviso_parcela = ""
                if dfm_row:
                    _pt = dfm_row["parcelas_totais"]
                    _pp = int(dfm_row["parcelas_pagas"] or 0)
                    _qtd_v = max(1, len(dfm_row["dias_vencimento"] or [1]))
                    _val_mensal = Decimal(str(dfm_row["valor_mensal"]))
                    _val_parc = (_val_mensal / Decimal(str(_qtd_v))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                    # Se a retirada cobre pelo menos 90% de uma parcela, computa a baixa manual da parcela
                    if valor >= (_val_parc * Decimal("0.90")):
                        nova_pp = _pp + 1
                        if nova_pp >= _pt:
                            await conn.execute(
                                "UPDATE public.despesas_fixas_mensais SET parcelas_pagas = $1, ativo = FALSE WHERE id = $2::uuid;",
                                nova_pp, str(dfm_row["id"])
                            )
                            # Se a caixinha ficou vazia, auto-exclui (Auto-Limpeza)
                            if novo_saldo <= Decimal("0.01"):
                                await conn.execute(
                                    "DELETE FROM public.caixas_provisao WHERE id = $1::uuid;",
                                    str(row["id"])
                                )
                                aviso_parcela = (
                                    f"\n\n🏁  *Parcelamento de {dfm_row['nome']} concluído!* "
                                    f"Todas as {_pt} parcelas foram quitadas. "
                                    f"Como o saldo zerou, a caixinha foi arquivada automaticamente."
                                )
                            else:
                                aviso_parcela = (
                                    f"\n\n🏁  *Parcelamento de {dfm_row['nome']} concluído!* "
                                    f"Todas as {_pt} parcelas foram quitadas. A despesa foi desativada."
                                )
                        else:
                            await conn.execute(
                                "UPDATE public.despesas_fixas_mensais SET parcelas_pagas = $1 WHERE id = $2::uuid;",
                                nova_pp, str(dfm_row["id"])
                            )
                            aviso_parcela = (
                                f"\n\n💳  *Parcela ({nova_pp}/{_pt}) de {dfm_row['nome']} quitada!* "
                                f"Contador de parcelas atualizado para evitar cobrança duplicada."
                            )

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"retirada_caixa_{nome}", "saldo_atual", -valor
            )
            meta = float(row["meta_valor"]) if row["meta_valor"] else None
            meta_str = ""
            if meta and float(novo_saldo) < meta:
                meta_str = f"\n_A caixinha voltará a acumular nos próximos fechamentos._"
            return (
                f"✅  *Retirada registrada!*\n"
                f"• Caixa:  *{nome}*\n"
                f"• Valor retirado:  *R$ {float(valor):.2f}*\n"
                f"• Novo saldo:  *R$ {float(novo_saldo):.2f}*\n\n"
                f"_Use este dinheiro para pagar a despesa real quando chegar o vencimento._{meta_str}"
                + aviso_parcela
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao retirar da caixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao processar a retirada. Tente novamente."

    @staticmethod
    async def _excluir_caixa(motorista_id: str, tenant_id: str, nome: str) -> str:
        """Exclui permanentemente uma caixa de provisão.

        Só bloqueia se houver saldo materialmente positivo (> R$ 0,01) — saldos
        residuais de arredondamento (ex: R$ 0,00 reportado como -0,001 no banco)
        são ignorados para não gerar instruções absurdas como '!retirar caixa X 0.00'.
        Desvincula despesas_fixas_mensais (caixa_id → NULL) antes do DELETE para
        evitar violação de integridade referencial.
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    "SELECT id, saldo_atual FROM public.caixas_provisao "
                    "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
                    motorista_id, nome,
                )
                if not row:
                    return (
                        f"⚠  Nenhuma caixa chamada  *{nome}*  encontrada.\n"
                        f"Envie  *!caixas*  para ver a lista."
                    )

                # Tolerância de R$ 0,01 para imprecisões de arredondamento acumuladas.
                # Sem isso, saldo de R$ 0,001 bloqueia exclusão e sugere retirar R$ 0,00.
                saldo = Decimal(str(row["saldo_atual"]))
                _TOLERANCIA = Decimal("0.01")
                if saldo > _TOLERANCIA:
                    saldo_fmt = f"R$ {float(saldo):.2f}".replace(".", ",")
                    return (
                        f"⚠️  *{nome}* ainda tem saldo!  *{saldo_fmt}*\n\n"
                        f"Retire antes de excluir:\n"
                        f"  👉  `!retirar caixa {nome} {float(saldo):.2f}`\n\n"
                        f"_Ou, se quiser excluir mesmo assim e perder o saldo, "
                        f"retire primeiro com o comando acima._"
                    )

                # Desvincula despesas ativas que apontam para esta caixa e conta quantas foram
                desp_desvinculadas_rows = await conn.fetch(
                    """
                    UPDATE public.despesas_fixas_mensais SET caixa_id = NULL
                    WHERE motorista_id = $1::uuid AND caixa_id = $2::uuid AND ativo = TRUE
                    RETURNING id;
                    """,
                    motorista_id, str(row["id"]),
                )
                desp_desvinculadas = len(desp_desvinculadas_rows)

                await conn.execute(
                    "DELETE FROM public.caixas_provisao WHERE id = $1::uuid;",
                    str(row["id"]),
                )

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"excluir_caixa_{nome}", "deleted", True
            )
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")

            nota_desv = (
                f"\n_Despesas vinculadas foram desvinculadas — continue acumulando em outra caixa ou crie uma nova._"
                if desp_desvinculadas > 0 else ""
            )
            return (
                f"🗑  *Caixa  {nome}  excluída!*{nota_desv}"
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao excluir caixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao excluir a caixa. Tente novamente."

    @staticmethod
    async def _remover_despesa_fixa(motorista_id: str, tenant_id: str, nome: str) -> str:
        """Desativa (soft-delete) uma despesa fixa pelo nome.

        Após desativar, verifica a caixa vinculada:
          • Saldo = 0 e sem outras despesas → exclui a caixa automaticamente (sem perda)
          • Saldo > 0 → informa o saldo existente e oferece os comandos de retirada/exclusão
          • Caixa vinculada a outras despesas ativas → desvincula mas mantém a caixa
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # Busca a despesa e a caixa vinculada antes de desativar
                desp_row = await conn.fetchrow(
                    """
                    SELECT id, caixa_id
                    FROM public.despesas_fixas_mensais
                    WHERE motorista_id = $1::uuid AND lower(nome) = lower($2) AND ativo = TRUE;
                    """,
                    motorista_id, nome,
                )
                if not desp_row:
                    return (
                        f"⚠  Nenhuma despesa fixa ativa com o nome  *{nome}*  foi encontrada.\n"
                        f"Envie  *!despesas fixas*  para ver a lista atual."
                    )

                despesa_id = str(desp_row["id"])
                caixa_id   = str(desp_row["caixa_id"]) if desp_row["caixa_id"] else None

                # Desativa a despesa
                await conn.execute(
                    "UPDATE public.despesas_fixas_mensais SET ativo = FALSE WHERE id = $1::uuid;",
                    despesa_id,
                )

                # Analisa o destino da caixa vinculada
                nota_caixa = ""
                if caixa_id:
                    caixa_row = await conn.fetchrow(
                        "SELECT nome_caixa, saldo_atual FROM public.caixas_provisao WHERE id = $1::uuid;",
                        caixa_id,
                    )
                    if caixa_row:
                        nome_cx  = caixa_row["nome_caixa"]
                        saldo_cx = Decimal(str(caixa_row["saldo_atual"]))

                        # Quantas despesas ATIVAS ainda apontam para esta caixa (exceto a que acabamos de remover)
                        outras_desp = await conn.fetchval(
                            """
                            SELECT COUNT(*) FROM public.despesas_fixas_mensais
                            WHERE caixa_id = $1::uuid AND ativo = TRUE AND id != $2::uuid;
                            """,
                            caixa_id, despesa_id,
                        ) or 0

                        _TOLERANCIA = Decimal("0.01")

                        if outras_desp > 0:
                            # Caixa ainda alimenta outra despesa — apenas desvincula esta despesa
                            await conn.execute(
                                "UPDATE public.despesas_fixas_mensais SET caixa_id = NULL WHERE id = $1::uuid;",
                                despesa_id,
                            )
                            nota_caixa = (
                                f"\n\n📦  _A caixinha  *{nome_cx}*  foi mantida — ela ainda está vinculada "
                                f"a {outras_desp} outra(s) despesa(s) ativa(s)._"
                            )
                        elif saldo_cx > _TOLERANCIA:
                            # Caixa fica órfã mas tem saldo — pergunta o que fazer
                            saldo_fmt = f"R$ {float(saldo_cx):.2f}".replace(".", ",")
                            nota_caixa = (
                                f"\n\n📦  *A caixinha  {nome_cx}  ainda tem  {saldo_fmt}  de saldo.*\n"
                                f"O que deseja fazer?\n"
                                f"  👉  `!retirar caixa {nome_cx} {float(saldo_cx):.2f}`  — resgatar o valor\n"
                                f"  👉  `!excluir caixa {nome_cx}`  — excluir após zerar"
                            )
                        else:
                            # Caixa vazia e órfã — exclui automaticamente
                            await conn.execute(
                                "DELETE FROM public.caixas_provisao WHERE id = $1::uuid;",
                                caixa_id,
                            )
                            nota_caixa = f"\n\n📦  _A caixinha  *{nome_cx}*  estava vazia e foi excluída automaticamente._"

            await ParametrosService._registrar_auditoria(
                tenant_id, motorista_id, f"remover_despesa_{nome}", "ativo", False
            )
            await RedisFSMService.limpar_buffer(f"profile:{tenant_id}")
            return (
                f"✅  Despesa  *{nome}*  removida!\n"
                f"_Não será mais deduzida nos próximos fechamentos._"
                + nota_caixa
            )
        except Exception as exc:
            logger.error(f"[ParametrosService] Erro ao remover despesa fixa (motorista={motorista_id}): {exc}")
            return "❌ Erro ao remover a despesa fixa. Tente novamente em instantes."
