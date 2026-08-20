import logging
import os
import re
import unicodedata
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.transacao_service import TransacaoService
from services.turno_service import TurnoService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

HTTP_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
HTTP_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando CFO Inteligente B2C API.")
    app.state.http_client = httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        limits=HTTP_LIMITS,
    )

    await RedisFSMService.init_redis()
    await DatabaseService.initialize_pool()

    try:
        yield
    finally:
        logger.info("Desligando CFO Inteligente B2C API.")
        await app.state.http_client.aclose()
        await RedisFSMService.close_redis()
        await DatabaseService.close_pool()


app = FastAPI(
    title="CFO Inteligente B2C API",
    version="5.1.0",
    lifespan=lifespan,
)


def normalizar_texto(texto: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )


def converter_para_float(texto_valor: str) -> float:
    try:
        limpo = texto_valor.replace("R$", "").strip()

        if "," in limpo and "." in limpo:
            if limpo.find(".") < limpo.find(","):
                limpo = limpo.replace(".", "").replace(",", ".")
            else:
                limpo = limpo.replace(",", "")
        elif "," in limpo:
            limpo = limpo.replace(",", ".")

        return float(limpo)
    except (ValueError, AttributeError):
        return 0.0


def formatar_moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_km(valor: float) -> str:
    return f"{valor:,.1f}".replace(",", ".")


def formatar_relatorio_fechamento_dre(nome_motorista: str, res: Dict[str, Any]) -> str:
    horas = int(res["tempo_total_min"] // 60)
    minutos = int(res["tempo_total_min"] % 60)
    duracao_str = f"{horas}h {minutos}min" if horas > 0 else f"{minutos} min"

    km_rodados = res["km_rodados"] if res["km_rodados"] > 0 else 1.0
    horas_trab = res["horas_trabalhadas"] if res["horas_trabalhadas"] > 0 else 1.0
    faturamento = res["faturamento_bruto"]
    custo_variavel = res["custo_variavel"]
    custo_fixo = res["custo_fixo_rateado"]
    lucro = res["lucro_liquido_real"]

    faturamento_por_km = faturamento / km_rodados
    faturamento_por_hora = faturamento / horas_trab
    lucro_por_hora = lucro / horas_trab
    margem_lucro = (lucro / faturamento * 100.0) if faturamento > 0 else 0.0
    meta_diaria = res["meta_mensal"] / res["dias_uteis"] if res["dias_uteis"] > 0 else 0.0
    perc_meta = (faturamento / meta_diaria * 100.0) if meta_diaria > 0 else 0.0

    despesas = res.get("despesas_detalhadas", [])
    if despesas:
        detalhes_despesas = "• Detalhes dos Gastos:"
        for despesa in despesas:
            descricao = despesa.get("descricao_original") or despesa.get("categoria", "geral")
            valor = float(despesa.get("valor", 0.0))
            detalhes_despesas += f"  - _{descricao}_: *{formatar_moeda(valor)}*"
    else:
        detalhes_despesas = "• Nenhuma despesa registada neste turno."

    return (
        "🏁 *FECHAMENTO DE TURNO - DRE EXECUTIVO DIÁRIO*"
        f"👤 Motorista: *{nome_motorista}*"
        "──────────────────────────────"
        "⏱️ *1. RESUMO OPERACIONAL*"
        f"• Horário: *{res['data_inicio']}* às *{res['data_fim']}* ({duracao_str})"
        f"• Odômetro: *{formatar_km(res['km_inicial'])} km* ➔ *{formatar_km(res['km_final'])} km*"
        f"• Distância Rodada: *{formatar_km(km_rodados)} km*"
        f"📊 *2. DEMONSTRATIVO DE RESULTADO (DRE)*"
        f"• (+) Faturamento Bruto: *{formatar_moeda(faturamento)}*"
        f"• (-) Custos Variáveis:"
        f"{detalhes_despesas}"
        f"  *Total Custos Variáveis: {formatar_moeda(custo_variavel)}*"
        f"• (=) Margem Contribuição: *{formatar_moeda(faturamento - custo_variavel)}*"
        f"• (-) Rateio Custo Fixo (Aluguel/Pro-Rata): *{formatar_moeda(custo_fixo)}*"
        f"──────────────────────────────"
        f"💰 *LUCRO LÍQUIDO REAL DO DIA: {formatar_moeda(lucro)}*"
        f"📈 Margem Líquida Real: *{margem_lucro:.1f}%*"
        f"🎯 *3. INDICADORES DE PERFORMANCE*"
        f"• Faturamento por KM: *{formatar_moeda(faturamento_por_km)}/km*"
        f"• Faturamento por Hora: *{formatar_moeda(faturamento_por_hora)}/h*"
        f"• Lucro Real por Hora: *{formatar_moeda(lucro_por_hora)}/h*"
        f"• Atingimento Meta Diária ({formatar_moeda(meta_diaria)}): *{perc_meta:.1f}%*"
        f"🛡️ *Cofre Contábil Atualizado! Fechamento registado com sucesso. Bom descanso!*"
    )


async def enviar_whatsapp(app: FastAPI, remote_jid: str, texto: str) -> None:
    try:
        await app.state.http_client.post(
            f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
            headers={
                "apikey": EVOLUTION_API_KEY,
                "Content-Type": "application/json",
            },
            json={"number": remote_jid, "text": texto},
        )
    except httpx.HTTPError:
        logger.exception("Falha ao enviar mensagem WhatsApp. remote_jid=%s", remote_jid)


def extrair_texto_mensagem(data: Dict[str, Any]) -> str:
    return (
        data.get("message", {}).get("conversation")
        or data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
        or ""
    ).strip()


def extrair_km_do_texto(texto_limpo: str) -> Optional[float]:
    numeros = re.findall(r"d+", texto_limpo.replace(".", "").replace(",", ""))
    return next((float(num) for num in numeros if float(num) > 100), None)


@app.post("/webhooks/evolution")
@app.post("/api/v1/webhook/whatsapp")
async def evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        data = body.get("data", {})

        if data.get("key", {}).get("fromMe", False):
            return {"status": "ignored"}

        remote_jid = data.get("key", {}).get("remoteJid", "")
        tenant_id = remote_jid.split("@")[0] if remote_jid else "unknown"
        wpp_msg_id = data.get("key", {}).get("id", "unknown")
        texto_bruto = extrair_texto_mensagem(data)

        if not texto_bruto or tenant_id == "unknown":
            return {"status": "ignored"}

        texto_limpo = normalizar_texto(texto_bruto)
        motorista = await DatabaseService.buscar_motorista_por_telefone(tenant_id)

        if not motorista:
            background_tasks.add_task(
                enviar_whatsapp,
                app,
                remote_jid,
                "Fala, motorista! Registo não encontrado. Aciona o suporte para ser registado.",
            )
            return {"status": "unregistered"}

        motorista_id = str(motorista["id"])
        nome_motorista = motorista["nome"]
        fsm_turno_key = f"turno_flow:{tenant_id}"

        if any(t in texto_limpo for t in ["cancelar", "esquece", "abortar", "parar"]):
            await RedisFSMService.resetar_fluxo(fsm_turno_key)
            background_tasks.add_task(
                enviar_whatsapp,
                app,
                remote_jid,
                "✅ Ação cancelada com sucesso. O que faremos agora?",
            )
            return {"status": "cancelled"}

        estado_turno = await RedisFSMService.obter_estado(fsm_turno_key)
        if estado_turno in {"AGUARDANDO_KM_INICIAL", "AGUARDANDO_KM_FINAL"}:
            km_digitado = converter_para_float(texto_bruto)

            if km_digitado <= 0:
                background_tasks.add_task(
                    enviar_whatsapp,
                    app,
                    remote_jid,
                    "⚠️ Por favor, envia apenas número válido da quilometragem atual do painel (ex: 1399).",
                )
                return {"status": "fsm_waiting_km"}

            if estado_turno == "AGUARDANDO_KM_INICIAL":
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    await RedisFSMService.resetar_fluxo(fsm_turno_key)
                    resposta = "⚠️ Nenhum veículo cadastrado na sua conta."
                else:
                    res = await TurnoService.abrir_turno(
                        motorista_id=motorista_id,
                        veiculo_id=str(veiculo["id"]),
                        km_inicial=km_digitado,
                    )
                    if res["status"] == "success":
                        await RedisFSMService.resetar_fluxo(fsm_turno_key)
                        resposta = (
                            f"🚀 Turno aberto! Odômetro: *{formatar_km(km_digitado)} km*. "
                            f"Boa jornada, {nome_motorista}!"
                        )
                    else:
                        if res.get("error_code") == "TURNO_JA_ATIVO":
                            await RedisFSMService.resetar_fluxo(fsm_turno_key)
                        else:
                            await RedisFSMService.definir_estado(
                                fsm_turno_key,
                                "AGUARDANDO_KM_INICIAL",
                            )
                        resposta = res["message"]
            else:
                res = await TurnoService.fechar_turno_com_dre(motorista_id, km_digitado)
                if res["status"] == "success":
                    await RedisFSMService.resetar_fluxo(fsm_turno_key)
                    resposta = formatar_relatorio_fechamento_dre(nome_motorista, res)
                else:
                    if res.get("error_code") == "ODOMETRO_DIVERGENTE":
                        await RedisFSMService.definir_estado(
                            fsm_turno_key,
                            "AGUARDANDO_KM_FINAL",
                        )
                    else:
                        await RedisFSMService.resetar_fluxo(fsm_turno_key)
                    resposta = res["message"]

            background_tasks.add_task(enviar_whatsapp, app, remote_jid, resposta)
            return {"status": "fsm_km_processed"}

        tem_intencao_inicio = any(
            w in texto_limpo for w in ["iniciar", "comecar", "abrir", "bora", "turno", "inicio"]
        )
        tem_intencao_fim = any(
            w in texto_limpo for w in ["encerrar", "fechar", "finalizar", "terminar", "fim"]
        )

        if tem_intencao_inicio or tem_intencao_fim:
            await RedisFSMService.limpar_buffer(fsm_turno_key)

        km_encontrado = extrair_km_do_texto(texto_limpo)

        if tem_intencao_inicio:
            if km_encontrado:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if veiculo:
                    res = await TurnoService.abrir_turno(
                        motorista_id=motorista_id,
                        veiculo_id=str(veiculo["id"]),
                        km_inicial=km_encontrado,
                    )
                    resposta = (
                        f"🚀 Turno aberto! Odômetro: *{formatar_km(km_encontrado)} km*. "
                        f"Boa jornada, {nome_motorista}!"
                        if res["status"] == "success"
                        else res["message"]
                    )
                else:
                    resposta = "⚠️ Nenhum veículo cadastrado na sua conta."
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                resposta = "🟢 Beleza! Qual é a *quilometragem atual* do painel? (Ex: 1399)"

            background_tasks.add_task(enviar_whatsapp, app, remote_jid, resposta)
            return {"status": "turno_start_intent"}

        if tem_intencao_fim:
            if km_encontrado:
                res = await TurnoService.fechar_turno_com_dre(motorista_id, km_encontrado)
                resposta = (
                    formatar_relatorio_fechamento_dre(nome_motorista, res)
                    if res["status"] == "success"
                    else res["message"]
                )
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                resposta = "🏁 Para gerar o seu DRE diário, qual é a *quilometragem final* do painel?"

            background_tasks.add_task(enviar_whatsapp, app, remote_jid, resposta)
            return {"status": "turno_end_intent"}

        tem_intencao_pausa = any(w in texto_limpo for w in ["pausar", "pausa"])
        tem_intencao_retomar = any(w in texto_limpo for w in ["retomar", "voltar"])
        tem_intencao_status = any(w in texto_limpo for w in ["status", "resumo"])

        if tem_intencao_pausa:
            await RedisFSMService.resetar_fluxo(fsm_turno_key)
            res = await TurnoService.pausar_turno(motorista_id)
            background_tasks.add_task(enviar_whatsapp, app, remote_jid, res["message"])
            return {"status": "turno_paused"}

        if tem_intencao_retomar:
            await RedisFSMService.resetar_fluxo(fsm_turno_key)
            res = await TurnoService.retomar_turno(motorista_id)
            background_tasks.add_task(enviar_whatsapp, app, remote_jid, res["message"])
            return {"status": "turno_resumed"}

        if tem_intencao_status:
            await RedisFSMService.resetar_fluxo(fsm_turno_key)
            res = await TurnoService.obter_status_turno(motorista_id)
            background_tasks.add_task(enviar_whatsapp, app, remote_jid, res["message"])
            return {"status": "turno_status"}

        palavras_chave_financeiras = [
            "recebi",
            "ganhei",
            "faturei",
            "corrida",
            "uber",
            "99",
            "gastei",
            "paguei",
            "despesa",
            "combustiv",
            "gasolin",
            "posto",
            "almoco",
            "bala",
            "lava",
            "marmita",
            "mercado",
        ]
        is_financeiro = any(w in texto_limpo for w in palavras_chave_financeiras)
        valor_transacao = converter_para_float(texto_bruto)

        if is_financeiro and valor_transacao > 0:
            is_despesa = any(
                w in texto_limpo
                for w in [
                    "gastei",
                    "paguei",
                    "despesa",
                    "combustiv",
                    "gasolin",
                    "posto",
                    "almoco",
                    "bala",
                    "lava",
                    "marmita",
                    "mercado",
                ]
            )

            if is_despesa:
                categoria = (
                    "combustivel"
                    if any(c in texto_limpo for c in ["gasolin", "posto", "combustiv"])
                    else "geral"
                )
                res = await TransacaoService.registrar_transacao(
                    motorista_id=motorista_id,
                    tipo_movimentacao="despesa",
                    categoria=categoria,
                    valor=valor_transacao,
                    descricao=texto_bruto,
                    wpp_msg_id=wpp_msg_id,
                )
                resposta = (
                    f"✅ Despesa de *{formatar_moeda(valor_transacao)}* registada com sucesso! 🛡️"
                    if res["status"] == "success"
                    else res["message"]
                )
            else:
                res = await TransacaoService.registrar_transacao(
                    motorista_id=motorista_id,
                    tipo_movimentacao="receita",
                    categoria="corrida",
                    valor=valor_transacao,
                    descricao=texto_bruto,
                    wpp_msg_id=wpp_msg_id,
                )
                resposta = (
                    f"✅ Receita de *{formatar_moeda(valor_transacao)}* guardada no cofre! 🛡️"
                    if res["status"] == "success"
                    else res["message"]
                )

            background_tasks.add_task(enviar_whatsapp, app, remote_jid, resposta)
            return {"status": "finance_logged"}

        resposta_ajuda = (
            "🤖 Não reconheci a ação! Aqui tens os comandos rápidos:"
            "🟢 *Iniciar* (ou 'iniciar 1399')"
            "🏁 *Fechar* (ou 'fechar 1450')"
            "⏸️ *Pausar* / *Retomar*"
            "📊 *Status* (resumo do dia)"
            "💰 *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')"
            "Como posso ajudar agora?"
        )
        background_tasks.add_task(enviar_whatsapp, app, remote_jid, resposta_ajuda)
        return {"status": "received"}

    except Exception:
        logger.exception("Erro crítico no webhook /webhooks/evolution")
        return {"status": "error", "detail": "internal_server_error"}
