import os
import re
import json
import unicodedata
import logging

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from contextlib import asynccontextmanager

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.transacao_service import TransacaoService
from services.turno_service import TurnoService
from services.help_service import HelpService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

# ---------------------------------------------------------------------------
# Ciclo de vida da aplicação
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Inicializando stack CFO Inteligente B2C...")
    await DatabaseService.initialize_pool()
    yield
    logger.info("Encerrando conexões graciosamente...")
    await DatabaseService.close_pool()
    try:
        await RedisFSMService.close_client()
    except Exception as exc:
        logger.error("Falha ao fechar Redis: %s", exc)


app = FastAPI(
    title="CFO Inteligente B2C API",
    description="SaaS financeiro e ERP logístico conversacional via WhatsApp para motoristas de aplicativo.",
    version="6.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Utilitários de formatação e parsing
# ---------------------------------------------------------------------------

def normalizar_texto(texto: str) -> str:
    """Remove acentos e pontuação para reconhecimento de intenção sem erros de encoding."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9\s]", "", sem_acento).lower().strip()


def converter_para_float(texto_valor: str) -> float:
    """
    Parser monetário resiliente a formatos regionais brasileiros.
    Ex: 'R$ 1.500,50' → 1500.50; '1500.50' → 1500.50; '1.500' → 1500.
    """
    try:
        limpo = texto_valor.upper().replace("R$", "").replace("REAIS", "").strip()
        limpo = re.sub(r"[^\d.,]", "", limpo)
        if "," in limpo and "." in limpo:
            if limpo.find(".") < limpo.find(","):
                # Padrão BR: 1.000,50
                limpo = limpo.replace(".", "").replace(",", ".")
            else:
                # Padrão US: 1,000.50
                limpo = limpo.replace(",", "")
        elif "," in limpo:
            limpo = limpo.replace(",", ".")
        return float(limpo)
    except ValueError:
        return 0.0


def formatar_relatorio_parcial(nome_motorista: str, info: dict) -> str:
    """Gera o status parcial do turno formatado para o WhatsApp."""
    aluguel_diario = info["custo_aluguel_semanal"] / 6.0
    franquia_diaria = info["franquia_km_semanal"] / 7.0
    meta_diaria = info["meta_mensal"] / info["dias_uteis"]
    km_ini_fmt = f"{info['km_inicial']:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")

    return (
        f"📥 *DADOS PARCIAIS DO TURNO DE HOJE ({info['data_turno']})*\n\n"
        f"• Início: *{info['data_inicio_hora']}*\n"
        f"• KM Inicial: *{km_ini_fmt} km*\n"
        f"• Combustível Lançado: *R$ {info['total_abastecido']:.2f}*\n\n"
        f"⚙️ *CUSTOS DO CONTRATO ({info['locadora']})*\n\n"
        f"• Escala: *{info['escala_trabalho']}*\n"
        f"• Aluguel Diário: *R$ {aluguel_diario:.2f}* (semanal R$ {info['custo_aluguel_semanal']:.2f} / 6d)\n"
        f"• Franquia Recomendada: *{franquia_diaria:.0f} km/dia*\n"
        f"• KM Excedente: *R$ {info['valor_km_excedente']:.2f}/km*\n\n"
        f"🎯 *METAS DE HOJE*\n\n"
        f"• Meta Diária: *R$ {meta_diaria:.2f}*\n"
        f"• Piso Mínimo Indicado: *R$ 2,00/km* | *R$ 30,00/h*\n\n"
        f"🔮 Para encerrar a sua jornada e emitir o DRE, envie: *'fechar [KM final]'*"
    )


def formatar_relatorio_fechamento_dre(nome_motorista: str, res: dict) -> str:
    """
    Formata o DRE Executivo Diário para exibição no WhatsApp.
    Utiliza o nome real do motorista (passado como parâmetro) — nunca hardcoded.
    Aplica formatação numérica brasileira via triple-replace para evitar duplos pontos.
    """
    horas = int(res["tempo_total_min"] // 60)
    minutos = int(res["tempo_total_min"] % 60)
    duracao_str = f"{horas}h {minutos}min" if horas > 0 else f"{minutos} min"

    km_rodados = res["km_rodados"]
    horas_trab = res["horas_trabalhadas"] if res["horas_trabalhadas"] > 0 else 1.0
    fat = res["faturamento_bruto"]
    c_var = res["custo_variavel"]
    c_fixo = res["custo_fixo_rateado"]
    lucro = res["lucro_liquido_real"]

    faturamento_por_km = (fat / km_rodados) if km_rodados > 0 else 0.0
    faturamento_por_hora = fat / horas_trab
    lucro_por_hora = lucro / horas_trab
    margem_lucro = (lucro / fat * 100.0) if fat > 0 else 0.0
    meta_diaria = res["meta_mensal"] / res["dias_uteis"]
    perc_meta = (fat / meta_diaria * 100.0) if meta_diaria > 0 else 0.0

    lista_despesas_str = ""
    despesas = res.get("despesas_detalhadas", [])
    if despesas:
        lista_despesas_str = "• Detalhes dos Gastos:\n"
        for d in despesas:
            desc = d.get("descricao_original") or d.get("categoria", "geral")
            val = float(d.get("valor", 0.0))
            lista_despesas_str += f"  - _{desc}_: *R$ {val:.2f}*\n"
    else:
        lista_despesas_str = "• Nenhuma despesa registrada neste turno.\n"

    # Rodapé de onboarding contratual — exibido apenas se o motorista ainda usa o contrato padrão
    rodape_sugestao = ""
    if not res.get("contrato_personalizado", False):
        rodape_sugestao = (
            "\n\n"
            f"{nome_motorista}, este cálculo de hoje usou o custo padrão de *Aluguel da Zarp (R$ 170,14/dia)*. "
            "Mas queremos que seu lucro líquido seja 100% real. Como você trabalha nas ruas hoje? "
            "Escolha uma opção para ajustarmos o bot ao seu bolso:\n\n"
            "1️⃣ *Carro Alugado* (Zarp, Movida, Mottu, etc.):\n"
            "👉 Envie: *'atualizar contrato [Locadora] [Aluguel Semanal] [Franquia KM]'* "
            "(ex: *atualizar contrato Zarp 1020 1500*)\n\n"
            "2️⃣ *Carro Próprio Quitado* (sem mensalidade, apenas provisão de manutenção):\n"
            "👉 Envie: *'atualizar contrato Proprietario [Manutenção Diária] 0'* "
            "(ex: *atualizar contrato Proprietario 15 0*)\n\n"
            "3️⃣ *Carro Financiado* (mensalidade + manutenção):\n"
            "👉 Envie: *'atualizar contrato Financiado [Pro-Rata Mensalidade + Manutenção] 0'* "
            "(ex: *atualizar contrato Financiado 45 0*)"
        )

    km_ini_fmt = f"{res['km_inicial']:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    km_fin_fmt = f"{res['km_final']:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    km_rod_fmt = f"{km_rodados:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")

    return (
        f"🏁 *FECHAMENTO DE TURNO - DRE EXECUTIVO DIÁRIO*\n"
        f"👤 Motorista: *{nome_motorista}*\n"
        f"──────────────────────────────\n\n"
        f"⏱️ *1. RESUMO OPERACIONAL*\n"
        f"• Horário: *{res['data_inicio']}* às *{res['data_fim']}* ({duracao_str})\n"
        f"• Odômetro: *{km_ini_fmt} km* ➔ *{km_fin_fmt} km*\n"
        f"• Distância Rodada: *{km_rod_fmt} km*\n\n"
        f"📊 *2. DEMONSTRATIVO DE RESULTADO (DRE)*\n"
        f"• (+) Faturamento Bruto: *R$ {fat:.2f}*\n"
        f"• (-) Custos Variáveis:\n"
        f"{lista_despesas_str}"
        f"  *Total Custos Variáveis: R$ {c_var:.2f}*\n"
        f"• (=) Margem Contribuição: *R$ {(fat - c_var):.2f}*\n"
        f"• (-) Rateio Custo Fixo (Aluguel/Pro-Rata): *R$ {c_fixo:.2f}*\n"
        f"──────────────────────────────\n"
        f"💰 *LUCRO LÍQUIDO REAL DO DIA: R$ {lucro:.2f}*\n"
        f"📈 Margem Líquida Real: *{margem_lucro:.1f}%*\n\n"
        f"🎯 *3. INDICADORES DE PERFORMANCE*\n"
        f"• Faturamento por KM: *R$ {faturamento_por_km:.2f}/km*\n"
        f"• Faturamento por Hora: *R$ {faturamento_por_hora:.2f}/h*\n"
        f"• Lucro Real por Hora: *R$ {lucro_por_hora:.2f}/h*\n"
        f"• Rendimento Médio do Turno: *{res.get('km_por_litro', 0.0):.2f} km/L*\n"
        f"• Atingimento Meta Diária (R$ {meta_diaria:.2f}): *{perc_meta:.1f}%*\n\n"
        f"🛡️ *Cofre Contábil Atualizado! Fechamento registrado com sucesso. Bom descanso!*"
        f"{rodape_sugestao}"
    )


# ---------------------------------------------------------------------------
# Integração com Evolution API (WhatsApp Gateway)
# ---------------------------------------------------------------------------

async def enviar_whatsapp(remote_jid: str, texto: str):
    """Envia mensagem de texto para o motorista via Evolution API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "text": texto},
                timeout=10.0,
            )
            response.raise_for_status()
    except Exception as exc:
        logger.error("[Evolution API] Falha ao enviar mensagem para %s: %s", remote_jid, exc)


async def enviar_status_digitando(remote_jid: str):
    """
    Sinaliza 'digitando...' no WhatsApp antes de operações pesadas para reter a atenção
    do motorista e evitar a falsa percepção de que o bot travou.
    """
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{EVOLUTION_API_URL}/chat/sendPresence/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "presence": "composing", "delay": 2000},
                timeout=5.0,
            )
    except Exception as exc:
        logger.error("[Evolution API] Falha ao simular presença para %s: %s", remote_jid, exc)


# ---------------------------------------------------------------------------
# Controlador de erros consecutivos (anti-loop FSM)
# ---------------------------------------------------------------------------

async def registrar_erro_e_verificar_escape(
    remote_jid: str, tenant_id: str, fsm_key: str, msg_erro: str
) -> bool:
    """
    Incrementa o contador de erros consecutivos do tenant.
    Ao atingir 3 erros, limpa o estado FSM, envia mensagem de escape e retorna True.
    Caso contrário, envia msg_erro e retorna False.
    """
    erros = await RedisFSMService.incrementar_erros_consecutivos(tenant_id)
    if erros >= 3:
        await RedisFSMService.limpar_buffer(fsm_key)
        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
        await enviar_whatsapp(
            remote_jid,
            "⚠️ *Múltiplos Erros Consecutivos!*\n"
            "Seu fluxo atual foi interrompido e limpo para evitar travamento.\n\n"
            "Por favor, envie um dos comandos rápidos para recomeçar:\n"
            "🟢 *Iniciar* (ou 'iniciar 1399')\n"
            "🏁 *Fechar* (ou 'fechar 1450')\n"
            "⏸️ *Pausar* / *Retomar*\n"
            "📊 *Status* (resumo do dia)\n"
            "💰 *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')",
        )
        return True
    await enviar_whatsapp(remote_jid, msg_erro)
    return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Verificação de saúde do microsserviço."""
    return {"status": "healthy", "engine": "FastAPI & PostgreSQL 15 Bank-Grade"}


# Roteador unificado de webhooks — aceita todos os caminhos conhecidos do Evolution
@app.post("/webhooks/evolution")
@app.post("/webhook/evolution")
@app.post("/api/v1/webhook/whatsapp")
@app.post("/webhook/whatsapp")
async def evolution_webhook_routing(request: Request, background_tasks: BackgroundTasks):
    """
    Gateway Conversacional de Bordas — processa mensagens recebidas do WhatsApp.

    Ordem de precedência absoluta de intenção:
      1. Auto-loop guard (fromMe)
      2. FSM de onboarding (motorista não cadastrado)
      3. Interceptor global de ajuda (stateless)
      4. Intercepção FSM de jornada (AGUARDANDO_KM_INICIAL / AGUARDANDO_KM_FINAL) — PRIORIDADE MÁXIMA:
         qualquer número digitado nesses estados é tratado como odômetro, bloqueando lançamentos avulsos.
      5. Atualização contratual
      6. Report de status parcial
      7. Gestão operacional de turno (iniciar, fechar, pausar, retomar)
      8. Lançamentos financeiros livres com trava de palavra-chave (anti-registro de número puro)
      9. Catch-all de ajuda contextual
    """
    try:
        try:
            body = await request.json()
        except Exception as exc:
            logger.error("Erro ao parsear payload JSON: %s", exc)
            return {"status": "error", "message": "Payload inválido"}

        data = body.get("data", {})

        # Blindagem de auto-loop — descarta mensagens enviadas pelo próprio bot
        if data.get("key", {}).get("fromMe", False):
            return {"status": "ignored", "reason": "message_from_bot_itself"}

        remote_jid = data.get("key", {}).get("remoteJid", "")
        tenant_id = remote_jid.split("@")[0] if remote_jid else "unknown"

        # wpp_msg_id None é tratado corretamente em TransacaoService (gera hash determinístico)
        wpp_msg_id = data.get("key", {}).get("id") or None

        texto_bruto = (
            data.get("message", {}).get("conversation")
            or data.get("message", {}).get("extendedTextMessage", {}).get("text")
            or data.get("message", {}).get("imageMessage", {}).get("caption")
            or ""
        ).strip()

        if not texto_bruto or tenant_id == "unknown":
            return {"status": "ignored", "reason": "missing_text_or_sender"}

        # UX: indica presença de digitação antes de operações pesadas
        background_tasks.add_task(enviar_status_digitando, remote_jid)

        texto_limpo = normalizar_texto(texto_bruto)
        motorista = await DatabaseService.buscar_motorista_por_telefone(tenant_id)

        # =========================================================================
        # FLUXO DE ONBOARDING CONVERSACIONAL (MOTORISTA NÃO CADASTRADO)
        # =========================================================================
        if not motorista:
            fsm_key = f"onboard:{tenant_id}"
            estado_atual = await RedisFSMService.obter_estado(fsm_key)

            if estado_atual == "IDLE" or not estado_atual:
                await RedisFSMService.definir_estado(fsm_key, "AGUARDANDO_NOME")
                background_tasks.add_task(
                    enviar_whatsapp,
                    remote_jid,
                    "Fala, motorista! Seja bem-vindo ao *CFO Inteligente B2C* 🚀\n"
                    "Percebi que você ainda não tem cadastro por aqui. Vamos resolver isso em 1 minuto!\n\n"
                    "Para começar, digite o seu *nome completo*:",
                )
                return {"status": "onboarding_step_nome"}

            elif estado_atual == "AGUARDANDO_NOME":
                if len(texto_bruto) < 3:
                    escapou = await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "⚠️ O seu nome deve conter pelo menos 3 caracteres. Digite novamente:",
                    )
                    return {"status": "onboarding_escaped" if escapou else "onboarding_invalid_name"}
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_VEICULO|name:{texto_bruto}")
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    f"Prazer em te conhecer, *{texto_bruto}*! 🚗\n"
                    "Qual é o *modelo e marca* do seu principal veículo de trabalho?",
                )
                return {"status": "onboarding_step_veiculo"}

            elif estado_atual.startswith("AGUARDANDO_VEICULO"):
                nome = estado_atual.split("|name:")[1] if "|name:" in estado_atual else "Motorista"
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_COMBUSTIVEL|name:{nome}|veiculo:{texto_bruto}")
                background_tasks.add_task(
                    enviar_whatsapp,
                    remote_jid,
                    f"Show! E qual é o tipo de combustível ou motorização do seu {texto_bruto}? ⛽\n\n"
                    "Responda exatamente com uma das opções:\n"
                    "👉 *Gasolina*\n"
                    "👉 *Etanol*\n"
                    "👉 *Flex*\n"
                    "👉 *Hibrido*\n"
                    "👉 *Eletrico*\n"
                    "👉 *GNV*",
                )
                return {"status": "onboarding_step_combustivel"}

            elif estado_atual.startswith("AGUARDANDO_COMBUSTIVEL"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]

                comb_input = texto_bruto.lower().replace("é", "e").replace("í", "i").strip()
                combustiveis_suportados = ["gasolina", "etanol", "flex", "hibrido", "eletrico", "gnv"]

                if comb_input not in combustiveis_suportados:
                    escapou = await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "⚠️ Por favor, responda exatamente com um dos combustíveis suportados: "
                        "*Gasolina, Etanol, Flex, Hibrido, Eletrico* ou *GNV*:",
                    )
                    return {"status": "onboarding_escaped" if escapou else "onboarding_invalid_fuel"}

                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(
                    fsm_key, f"AGUARDANDO_PLACA|name:{nome}|veiculo:{veiculo}|combustivel:{comb_input}"
                )
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    "Perfeito! E qual é a *placa do seu veículo*? (Ex: ABC-1234 ou ABC1D23)",
                )
                return {"status": "onboarding_step_placa"}

            elif estado_atual.startswith("AGUARDANDO_PLACA"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                combustivel = partes[3].split("combustivel:")[1]

                placa_limpa = re.sub(r"[^A-Za-z0-9]", "", texto_bruto).upper()
                if len(placa_limpa) != 7:
                    escapou = await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "⚠️ A placa deve conter exatamente 7 caracteres alfanuméricos. Digite novamente:",
                    )
                    return {"status": "onboarding_escaped" if escapou else "onboarding_invalid_plate"}

                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(
                    fsm_key,
                    f"AGUARDANDO_CAPACIDADE_TANQUE|name:{nome}|veiculo:{veiculo}|combustivel:{combustivel}|placa:{placa_limpa}",
                )
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    "Legal! Qual a *capacidade máxima do tanque* em litros?\n(Se for elétrico puro, responda *0*):",
                )
                return {"status": "onboarding_step_tanque"}

            elif estado_atual.startswith("AGUARDANDO_CAPACIDADE_TANQUE"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                combustivel = partes[3].split("combustivel:")[1]
                placa = partes[4].split("placa:")[1]

                tanque_val = converter_para_float(texto_bruto)
                # Elétrico puro pode ter tanque 0 (sem motor a combustão).
                # Todos os outros tipos exigem tanque > 0 para que o CMP seja calculável.
                tanque_minimo = 0 if combustivel == "eletrico" else 1
                if tanque_val < tanque_minimo:
                    escapou = await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "⚠️ A capacidade do tanque deve ser maior que zero para veículos a combustão. Digite novamente:",
                    )
                    return {"status": "onboarding_escaped" if escapou else "onboarding_invalid_tank"}
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)

                if combustivel in ("hibrido", "eletrico"):
                    await RedisFSMService.definir_estado(
                        fsm_key,
                        f"AGUARDANDO_CAPACIDADE_BATERIA|name:{nome}|veiculo:{veiculo}|combustivel:{combustivel}|placa:{placa}|tanque:{tanque_val}",
                    )
                    background_tasks.add_task(
                        enviar_whatsapp, remote_jid,
                        "E qual a *capacidade da bateria* em kWh?\n(Ex: 30):",
                    )
                    return {"status": "onboarding_step_bateria"}

                # Cadastro atômico para veículos de combustão ou GNV
                await enviar_whatsapp(remote_jid, "⚙️ *A preparar o seu cofre contábil... Só um segundo!*")
                try:
                    motorista_uuid = await DatabaseService.registrar_novo_motorista(
                        telefone=tenant_id, nome=nome, veiculo_modelo=veiculo,
                        combustivel=combustivel, placa=placa,
                    )
                    async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                        # Todas as especificações físicas do veículo residem no JSONB (sub-chave 'meta').
                        # Não há colunas físicas dedicadas na tabela veiculos.
                        estoque_dict = {
                            "meta": {
                                "tipo_veiculo": combustivel,
                                "is_flex": (combustivel == "flex"),
                                "is_hibrido": False,
                                "is_eletrico": False,
                                "capacidade_tanque_l": float(tanque_val),
                                "capacidade_bateria_kwh": 0.0,
                                "qtd_tanques": 1,
                            },
                            "liquido": {
                                "litros": 0.0, "custo_total": 0.0,
                                "gasolina_litros": 0.0, "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0,
                                "km_l_gasolina": 12.0, "km_l_etanol": 8.5,
                            },
                            "eletricidade": {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5},
                            "gnv": {"m3": 0.0, "custo_total": 0.0, "km_m3": 14.0},
                        }
                        await conn.execute(
                            """
                            UPDATE public.veiculos
                            SET estoque_financeiro = $1::jsonb
                            WHERE motorista_id = $2::uuid AND ativo = TRUE;
                            """,
                            json.dumps(estoque_dict),
                            motorista_uuid,
                        )
                except Exception as exc:
                    logger.error("Falha ao salvar onboarding no banco: %s", exc)

                await RedisFSMService.limpar_buffer(fsm_key)
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    f"Cadastro concluído com sucesso, {nome}! 🛡️\n"
                    f"O seu cofre contábil está ativo e configurado para o seu *{veiculo}* ({placa}).\n\n"
                    "Envie *'Iniciar'* acompanhado do seu odômetro atual para começar!",
                )
                return {"status": "onboarding_completed"}

            elif estado_atual.startswith("AGUARDANDO_CAPACIDADE_BATERIA"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                combustivel = partes[3].split("combustivel:")[1]
                placa = partes[4].split("placa:")[1]
                tanque_val = float(partes[5].split("tanque:")[1])

                bateria_val = converter_para_float(texto_bruto)
                if bateria_val < 0:
                    escapou = await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "⚠️ A capacidade não pode ser negativa. Digite novamente:",
                    )
                    return {"status": "onboarding_escaped" if escapou else "onboarding_invalid_battery"}
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)

                await enviar_whatsapp(remote_jid, "⚙️ *A preparar o seu cofre contábil... Só um segundo!*")
                try:
                    motorista_uuid = await DatabaseService.registrar_novo_motorista(
                        telefone=tenant_id, nome=nome, veiculo_modelo=veiculo,
                        combustivel=combustivel, placa=placa,
                    )
                    async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                        # Veículo híbrido ou elétrico: capacidade_bateria_kwh vem da resposta do onboarding.
                        # Todas as especificações físicas residem no JSONB (sub-chave 'meta').
                        estoque_dict = {
                            "meta": {
                                "tipo_veiculo": combustivel,
                                "is_flex": (combustivel == "flex"),
                                "is_hibrido": (combustivel == "hibrido"),
                                "is_eletrico": (combustivel == "eletrico"),
                                "capacidade_tanque_l": float(tanque_val),
                                "capacidade_bateria_kwh": float(bateria_val),
                                "qtd_tanques": 1,
                            },
                            "liquido": {
                                "litros": 0.0, "custo_total": 0.0,
                                "gasolina_litros": 0.0, "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0,
                                "km_l_gasolina": 12.0, "km_l_etanol": 8.5,
                            },
                            "eletricidade": {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5},
                            "gnv": {"m3": 0.0, "custo_total": 0.0, "km_m3": 14.0},
                        }
                        await conn.execute(
                            """
                            UPDATE public.veiculos
                            SET estoque_financeiro = $1::jsonb
                            WHERE motorista_id = $2::uuid AND ativo = TRUE;
                            """,
                            json.dumps(estoque_dict),
                            motorista_uuid,
                        )
                except Exception as exc:
                    logger.error("Falha ao salvar onboarding híbrido no banco: %s", exc)

                await RedisFSMService.limpar_buffer(fsm_key)
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    f"Cadastro concluído com sucesso, {nome}! 🛡️\n"
                    f"O seu cofre contábil está ativo e configurado para o seu *{veiculo}* ({placa}).\n\n"
                    "Envie *'Iniciar'* acompanhado do seu odômetro atual para começar!",
                )
                return {"status": "onboarding_completed"}

            # Estado FSM desconhecido no onboarding → reinicia
            await RedisFSMService.limpar_buffer(fsm_key)
            return {"status": "onboarding_state_reset"}

        # =========================================================================
        # MOTORISTAS CADASTRADOS — FSM DE TURNO
        # =========================================================================
        motorista_id = str(motorista["id"])
        fsm_turno_key = f"turno_flow:{tenant_id}"

        # =========================================================================
        # PASSO 0: INTERCEPTOR DE AJUDA GLOBAL (Stateless — não altera FSM)
        # =========================================================================
        palavras_ajuda = ["ajuda", "help", "socorro", "como", "explicar"]
        if any(w in texto_limpo for w in palavras_ajuda):
            partes = texto_limpo.split()
            topico = "geral"
            for i, palavra in enumerate(partes):
                if palavra in palavras_ajuda and i + 1 < len(partes):
                    topico_potencial = partes[i + 1]
                    if topico_potencial in ("metas", "contrato", "lancamentos", "turno"):
                        topico = topico_potencial
                        break
            background_tasks.add_task(enviar_whatsapp, remote_jid, HelpService.obter_ajuda(topico))
            return {"status": "help_provided", "topic": topico}

        estado_turno = await RedisFSMService.obter_estado(fsm_turno_key)

        # =========================================================================
        # PASSO 1: INTERCEPÇÃO FSM DE JORNADA — PRIORIDADE ABSOLUTA
        #
        # Se a sessão está aguardando odômetro (KM_INICIAL ou KM_FINAL), QUALQUER
        # número digitado pelo motorista é tratado estritamente como quilometragem.
        # O processamento de lançamentos financeiros avulsos é BLOQUEADO aqui.
        # =========================================================================
        estado_aguarda_km = estado_turno in ("AGUARDANDO_KM_INICIAL", "AGUARDANDO_KM_FINAL")
        # Usa str(estado_turno) defensivamente; obter_estado nunca retorna None mas pode
        # retornar "IDLE" — startswith em "IDLE" é seguro (retorna False).
        estado_confirmacao_zero = str(estado_turno).startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO")

        if estado_aguarda_km or estado_confirmacao_zero:

            # --- 1.1 Estado de Confirmação de Fechamento sem Lançamentos ---
            if estado_confirmacao_zero:
                if any(t in texto_limpo for t in ("confirmar", "confirm", "sim", "ok", "isso", "certeza")):
                    km_final_confirmado = float(estado_turno.split("|km:")[1])
                    await enviar_whatsapp(remote_jid, "📊 *Confirmado faturamento zerado. Gerando DRE definitivo...*")
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_final_confirmado)
                    await RedisFSMService.limpar_buffer(fsm_turno_key)
                    resposta = (
                        formatar_relatorio_fechamento_dre(motorista["nome"], res)
                        if res["sucesso"] else res["erro"]
                    )
                    background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
                    return {"status": "turno_closed_zero_confirmed"}

                # Motorista tentou registrar valor antes de confirmar
                valor_tentativa = converter_para_float(texto_bruto)
                palavras_financeiras = [
                    "recebi", "ganhei", "faturei", "corrida", "uber", "99",
                    "gastei", "paguei", "despesa", "combustiv", "gasolin", "posto",
                    "almoco", "bala", "lava", "marmita", "mercado",
                ]
                if any(w in texto_limpo for w in palavras_financeiras) and valor_tentativa > 0:
                    is_desp = any(
                        w in texto_limpo for w in
                        ("gastei", "paguei", "despesa", "combustiv", "gasolin", "posto",
                         "almoco", "bala", "lava", "marmita", "mercado")
                    )
                    cat_ev = (
                        "combustivel"
                        if any(c in texto_limpo for c in
                               ("gasolin", "posto", "combustiv", "etanol", "recarga", "kwh", "tomada"))
                        else "geral"
                    )
                    tipo_ev = "despesa" if is_desp else "receita"
                    await TransacaoService.registrar_transacao(
                        motorista_id, tipo_ev, cat_ev, valor_tentativa, texto_bruto, wpp_msg_id
                    )
                    resposta = (
                        f"✅ Lançamento de *R$ {valor_tentativa:.2f}* guardado! "
                        "Envie mais valores ou responda *'Confirmar'* para fechar o DRE:"
                    )
                else:
                    resposta = (
                        "Entendido. Registre mais transações (ex: *ganhei 150*) "
                        "ou responda *'Confirmar'* para fechar:"
                    )
                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
                return {"status": "waiting_transaction_entry"}

            # --- 1.2 Processamento de Odômetro (KM_INICIAL ou KM_FINAL) ---
            #
            # TRAVA DE ESCAPE: se o motorista digitar um comando global reconhecível
            # (iniciar, fechar, cancelar), limpa o estado FSM e redireciona.
            # Caso contrário, QUALQUER input é processado como quilometragem.
            if any(w in texto_limpo for w in ("cancelar", "cancel", "sair")):
                await RedisFSMService.limpar_buffer(fsm_turno_key)
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    "✅ Operação cancelada. Envie *'Iniciar'*, *'Fechar'* ou qualquer lançamento financeiro.",
                )
                return {"status": "fsm_cancelled"}

            km_digitado = converter_para_float(texto_bruto)

            if km_digitado > 100:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    background_tasks.add_task(
                        enviar_whatsapp, remote_jid,
                        "⚠️ Nenhum veículo ativo localizado no seu cadastro.",
                    )
                    return {"status": "error_no_vehicle"}

                if estado_turno == "AGUARDANDO_KM_INICIAL":
                    res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_digitado)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                        km_fmt = f"{km_digitado:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        resposta = (
                            f"🚀 Turno aberto! Odômetro inicial: *{km_fmt} km*. "
                            f"Boa jornada, {motorista['nome']}!"
                        )
                    else:
                        tipo_erro = res.get("tipo_erro", "")
                        if tipo_erro == "TURNO_JA_ATIVO":
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                            await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                            resposta = res["erro"]
                        else:
                            # ODOMETRO_DIVERGENTE: mantém FSM ativo e solicita correção
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                            escapou = await registrar_erro_e_verificar_escape(
                                remote_jid, tenant_id, fsm_turno_key, res["erro"]
                            )
                            if escapou:
                                return {"status": "fsm_escaped"}
                            return {"status": "fsm_km_processed"}

                elif estado_turno == "AGUARDANDO_KM_FINAL":
                    async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                        ativo = await conn.fetchrow(
                            "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid "
                            "AND status IN ('em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                            motorista_id,
                        )
                    if ativo:
                        total_tx = await TurnoService.verificar_transacoes_turno(motorista_id)
                        if total_tx == 0:
                            await RedisFSMService.definir_estado(
                                fsm_turno_key, f"AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO|km:{km_digitado}"
                            )
                            background_tasks.add_task(
                                enviar_whatsapp, remote_jid,
                                "⚠️ *Atenção, motorista!* Não encontrei nenhuma receita ou despesa registrada neste turno.\n\n"
                                "Tem certeza absoluta que o faturamento de hoje foi R$ 0,00?\n\n"
                                "Responda *'Confirmar'* para fechar assim mesmo ou envie o valor de uma despesa/receita.",
                            )
                            return {"status": "waiting_zero_transaction_confirmation"}

                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_digitado)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                        resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res)
                    else:
                        tipo_erro = res.get("tipo_erro", "")
                        if tipo_erro != "ODOMETRO_DIVERGENTE":
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                            await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                            resposta = res["erro"]
                        else:
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                            escapou = await registrar_erro_e_verificar_escape(
                                remote_jid, tenant_id, fsm_turno_key, res["erro"]
                            )
                            if escapou:
                                return {"status": "fsm_escaped"}
                            return {"status": "fsm_km_processed"}

                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
                return {"status": "fsm_km_processed"}

            else:
                # Número digitado ≤ 100 — improvável como odômetro; solicita correção
                escapou = await registrar_erro_e_verificar_escape(
                    remote_jid, tenant_id, fsm_turno_key,
                    "⚠️ Por favor, envie o número válido do odômetro atual do painel (ex: 13.990 ou 13990):",
                )
                return {"status": "fsm_escaped" if escapou else "fsm_invalid_km"}

        # =========================================================================
        # PASSO 2: ATUALIZAÇÃO CONTRATUAL EM TEMPO REAL
        # =========================================================================
        is_contrato = "atualizar contrato" in texto_limpo
        if is_contrato:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            partes = texto_bruto.split()
            try:
                locadora = partes[2] if len(partes) > 2 else "Localiza Zarp"
                aluguel_input = converter_para_float(partes[3]) if len(partes) > 3 else 1020.85
                franquia = converter_para_float(partes[4]) if len(partes) > 4 else 1505.00

                # Proprietário/Financiado: o valor passado é diário → multiplica por 6 para rateio
                if locadora.lower() in ("proprietario", "quitado", "financiado"):
                    aluguel_semanal = aluguel_input * 6.0
                else:
                    aluguel_semanal = aluguel_input

                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    await conn.execute(
                        """
                        UPDATE public.veiculos
                        SET locadora = $1, custo_aluguel_semanal = $2, franquia_km_semanal = $3,
                            contrato_personalizado = TRUE
                        WHERE motorista_id = $4::uuid AND ativo = TRUE;
                        """,
                        locadora, aluguel_semanal, franquia, motorista_id,
                    )
                    # Nota: contrato_personalizado é dado administrativo, não físico — mantém-se como coluna.
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    f"✅ Contrato atualizado para *{locadora}*! Aluguel rateado recalculado no cofre. 🛡️",
                )
            except Exception as exc:
                logger.error("Erro ao atualizar contrato: %s", exc)
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    "⚠️ Formato inválido. Use: *'atualizar contrato Zarp 1020.85 1505'*",
                )
            return {"status": "contract_updated"}

        # =========================================================================
        # PASSO 3: REPORT DE STATUS / RESUMO PARCIAL DO TURNO
        # =========================================================================
        is_status = any(t in texto_limpo for t in ("status", "resumo", "como estou", "parcial", "relatorio"))
        if is_status:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            try:
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    turno_ativo = await conn.fetchrow(
                        """
                        SELECT t.id, t.km_inicial, t.data_inicio,
                           v.locadora, v.custo_aluguel_semanal, v.franquia_km_semanal,
                           v.valor_km_excedente, v.escala_trabalho,
                           m.meta_mensal_faturamento, m.dias_uteis_mes
                        FROM public.turnos t
                        JOIN public.veiculos v ON v.id = t.veiculo_id
                        JOIN public.motoristas m ON m.id = t.motorista_id
                        WHERE t.motorista_id = $1::uuid AND t.status IN ('em_andamento', 'em_pausa')
                        ORDER BY t.data_inicio DESC LIMIT 1;
                        """,
                        motorista_id,
                    )
                    if turno_ativo:
                        turno_id = str(turno_ativo["id"])
                        dt_inicio = turno_ativo["data_inicio"]
                        tx = await conn.fetchrow(
                            "SELECT COALESCE(SUM(valor), 0.00) AS abastecido FROM public.transacoes "
                            "WHERE motorista_id = $1::uuid AND turno_id = $2::uuid AND categoria = 'combustivel' AND estornado = FALSE;",
                            motorista_id, turno_id,
                        )
                        info_turno = {
                            "data_turno": dt_inicio.strftime("%d/%m/%Y"),
                            "data_inicio_hora": dt_inicio.strftime("%H:%M"),
                            "km_inicial": float(turno_ativo["km_inicial"]),
                            "locadora": turno_ativo["locadora"] or "Localiza Zarp",
                            "custo_aluguel_semanal": float(turno_ativo["custo_aluguel_semanal"] or 1020.85),
                            "franquia_km_semanal": float(turno_ativo["franquia_km_semanal"] or 1505.0),
                            "valor_km_excedente": float(turno_ativo["valor_km_excedente"] or 0.75),
                            "escala_trabalho": turno_ativo["escala_trabalho"] or "De quarta a segunda (6 dias)",
                            "meta_mensal": float(turno_ativo["meta_mensal_faturamento"] or 12000.0),
                            "dias_uteis": int(turno_ativo["dias_uteis_mes"] or 26),
                            "total_abastecido": float(tx["abastecido"]),
                        }
                        resposta = formatar_relatorio_parcial(motorista["nome"], info_turno)
                    else:
                        resposta = "⚠️ Você não possui uma jornada em andamento. Envie *'Iniciar'* para abrir o turno!"
            except Exception as exc:
                logger.error("Erro ao obter status: %s", exc)
                resposta = "❌ Ocorreu um erro interno ao buscar seu status."
            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "status_sent"}

        # Pre-parse de intenção de jornada e odômetro inline
        # Nota: "turno" foi removido de tem_intencao_inicio para evitar que consultas como
        # "como está o turno?" disparem abertura acidental de nova jornada.
        tem_intencao_inicio = any(
            w in texto_limpo for w in ("iniciar", "comecar", "abrir", "bora", "inicio")
        )
        tem_intencao_fim = any(
            w in texto_limpo for w in ("encerrar", "fechar", "finalizar", "terminar", "fim")
        )
        tem_intencao_pausa = any(
            w in texto_limpo for w in ("pausa", "pausar", "pause", "pausei", "almocar")
        )
        tem_intencao_retomada = any(
            w in texto_limpo for w in ("retomar", "voltar", "voltei", "continuar", "retom")
        )

        # Extrai odômetro inline (número > 100 na mensagem).
        # Remove separadores de milhar (ponto e vírgula) ANTES de extrair os dígitos,
        # para que "13.990" ou "13,990" sejam lidos como 13990 e não como "13" e "990".
        texto_para_km = texto_limpo.replace(".", "").replace(",", "")
        numeros_msg = re.findall(r"\d+", texto_para_km)
        km_encontrado = next((float(n) for n in numeros_msg if float(n) > 100), None)

        # =========================================================================
        # PASSO 4: GESTÃO OPERACIONAL DE TURNO DIRETA
        # =========================================================================
        if tem_intencao_inicio:
            if km_encontrado:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    background_tasks.add_task(
                        enviar_whatsapp, remote_jid,
                        "⚠️ Nenhum veículo ativo localizado no seu cadastro.",
                    )
                    return {"status": "error_no_vehicle"}
                await enviar_whatsapp(remote_jid, "⏳ *A validar odômetro e abrindo turno...*")
                res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_encontrado)
                if res["sucesso"]:
                    await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                    km_fmt = f"{km_encontrado:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    resposta = (
                        f"🚀 Turno aberto! Odômetro inicial: *{km_fmt} km*. "
                        f"Boa jornada, {motorista['nome']}!"
                    )
                else:
                    tipo_erro = res.get("tipo_erro", "")
                    if tipo_erro != "TURNO_JA_ATIVO":
                        await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                    resposta = res["erro"]
                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    "🟢 Beleza! Qual é a *quilometragem atual* do painel? (Ex: 1399)",
                )
            return {"status": "turno_start_intent"}

        if tem_intencao_fim:
            if km_encontrado:
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    ativo = await conn.fetchrow(
                        "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid "
                        "AND status IN ('em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                        motorista_id,
                    )
                if ativo:
                    total_tx = await TurnoService.verificar_transacoes_turno(motorista_id)
                    if total_tx == 0:
                        await RedisFSMService.definir_estado(
                            fsm_turno_key, f"AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO|km:{km_encontrado}"
                        )
                        background_tasks.add_task(
                            enviar_whatsapp, remote_jid,
                            "⚠️ *Atenção, motorista!* Não encontrei nenhuma receita ou despesa registrada neste turno.\n\n"
                            "Tem certeza absoluta que o faturamento de hoje foi R$ 0,00?\n\n"
                            "Responda *'Confirmar'* para fechar assim mesmo ou envie o valor de uma despesa/receita.",
                        )
                        return {"status": "waiting_zero_transaction_confirmation"}

                await enviar_whatsapp(remote_jid, "📊 *A auditar movimentações e gerando DRE...*")
                res = await TurnoService.fechar_turno_com_dre(motorista_id, km_encontrado)
                resposta = (
                    formatar_relatorio_fechamento_dre(motorista["nome"], res)
                    if res["sucesso"] else res["erro"]
                )
                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                background_tasks.add_task(
                    enviar_whatsapp, remote_jid,
                    "🏁 Para gerar o seu DRE diário, qual é a *quilometragem final* no painel?",
                )
            return {"status": "turno_end_intent"}

        if tem_intencao_pausa:
            res = await TurnoService.pausar_turno(motorista_id)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
            resposta = (
                "⏸️ Turno pausado com sucesso. Quando voltar, envie *'retomar'*!"
                if res["sucesso"] else f"⚠️ {res['erro']}"
            )
            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "turno_paused"}

        if tem_intencao_retomada:
            res = await TurnoService.retomar_turno(motorista_id)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
            resposta = (
                "▶️ Turno retomado com sucesso! Bom trabalho."
                if res["sucesso"] else f"⚠️ {res['erro']}"
            )
            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "turno_resumed"}

        # =========================================================================
        # PASSO 5: LANÇAMENTOS FINANCEIROS LIVRES
        #
        # TRAVA DE PALAVRA-CHAVE: Para registrar um valor monetário, a mensagem DEVE
        # conter ao menos uma palavra financeira reconhecida. Mensagens com apenas números
        # (como odômetros ou placas) são silenciosamente ignoradas aqui.
        # =========================================================================
        palavras_chave_financeiras = (
            "recebi", "ganhei", "faturei", "corrida", "uber", "99", "indrive", "faturamento", "ganho",
            "gastei", "paguei", "despesa", "combustiv", "gasolin", "posto", "almoco",
            "bala", "lava", "marmita", "mercado", "oleo", "pneu", "etanol", "gnv",
            "recarga", "kwh", "solar", "tomada",
        )
        is_financeiro = any(w in texto_limpo for w in palavras_chave_financeiras)
        valor_transacao = converter_para_float(texto_bruto)

        if is_financeiro and valor_transacao > 0:
            is_despesa = any(
                w in texto_limpo for w in (
                    "gastei", "paguei", "despesa", "combustiv", "gasolin", "etanol", "gnv",
                    "reabastec", "recarga", "posto", "almoco", "bala", "lava",
                    "marmita", "mercado", "oleo", "pneu",
                )
            )
            tipo = "despesa" if is_despesa else "receita"

            if any(c in texto_limpo for c in
                   ("gasolin", "etanol", "gnv", "reabastec", "recarga", "posto",
                    "solar", "casa", "kwh", "tomada", "combustiv")):
                cat = "combustivel"
            elif any(c in texto_limpo for c in
                     ("almoco", "marmita", "lanche", "refeicao", "comida", "rango")):
                cat = "alimentacao"
            elif any(c in texto_limpo for c in
                     ("lava", "oleo", "pneu", "oficina", "mecanico", "manutencao")):
                cat = "manutencao"
            else:
                cat = "corrida" if tipo == "receita" else "geral"

            res_tx = await TransacaoService.registrar_transacao(
                motorista_id=motorista_id,
                tipo_movimentacao=tipo,
                categoria=cat,
                valor=valor_transacao,
                descricao=texto_bruto,
                wpp_msg_id=wpp_msg_id,
            )

            if res_tx.get("status") == "success":
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                val_fmt = f"R$ {valor_transacao:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                resposta = f"✅ Lançamento de *{val_fmt}* guardado com sucesso no cofre! 🛡️"
            elif res_tx.get("status") == "duplicate":
                resposta = "⚠️ Este lançamento já foi guardado anteriormente no cofre contábil."
            else:
                resposta = f"❌ Falha ao salvar no cofre contábil:\n_{res_tx.get('message')}_"

            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "finance_logged"}

        # =========================================================================
        # PASSO 6: CATCH-ALL — Ajuda Contextual
        # =========================================================================
        background_tasks.add_task(
            enviar_whatsapp,
            remote_jid,
            "🤖 Não reconheci a ação! Aqui estão os comandos rápidos:\n\n"
            "🟢 *Iniciar* (ou 'iniciar 1399')\n"
            "🏁 *Fechar* (ou 'fechar 1450')\n"
            "⏸️ *Pausar* / *Retomar*\n"
            "📊 *Status* (resumo do dia)\n"
            "💰 *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')\n\n"
            "Como posso ajudar agora?",
        )
        return {"status": "received"}

    except Exception as exc:
        logger.exception("Erro crítico no Webhook Evolution: %s", exc)
        return {"status": "error", "detail": str(exc)}
