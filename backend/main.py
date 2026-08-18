import os
import re
import unicodedata
import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from contextlib import asynccontextmanager

from services.redis_fsm import RedisFSMService
from services.database_service import DatabaseService
from services.turno_service import TurnoService
from services.transacao_service import TransacaoService

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Iniciando CFO Inteligente B2C (Núcleo FSM Estrita & DRE Completo Ativo)...")
    yield
    print("🛑 Desligando serviço...")

app = FastAPI(title="CFO Inteligente B2C API", version="4.8.2", lifespan=lifespan)

def normalizar_texto(texto: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', texto.lower()) if unicodedata.category(c) != 'Mn')

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
    except ValueError:
        return 0.0

def formatar_relatorio_fechamento_dre(nome_motorista: str, res: dict) -> str:
    horas = int(res["tempo_total_min"] // 60)
    minutos = int(res["tempo_total_min"] % 60)
    duracao_str = f"{horas}h {minutos}min" if horas > 0 else f"{minutos} min"

    km_rodados = res["km_rodados"] if res["km_rodados"] > 0 else 1.0
    horas_trab = res["horas_trabalhadas"] if res["horas_trabalhadas"] > 0 else 1.0
    fat = res["faturamento_bruto"]
    c_var = res["custo_variavel"]
    c_fixo = res["custo_fixo_rateado"]
    lucro = res["lucro_liquido_real"]

    faturamento_por_km = fat / km_rodados
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
            desc = d.get('descricao_original') or d.get('categoria', 'geral')
            val = float(d.get('valor', 0.0))
            lista_despesas_str += f"  - _{desc}_: *R$ {val:.2f}*\n"
    else:
        lista_despesas_str = "• Nenhuma despesa registada neste turno.\n"

    return (
        f"🏁 *FECHAMENTO DE TURNO - DRE EXECUTIVO DIÁRIO*\n"
        f"👤 Motorista: *{nome_motorista}*\n"
        f"──────────────────────────────\n\n"
        f"⏱️ *1. RESUMO OPERACIONAL*\n"
        f"• Horário: *{res['data_inicio']}* às *{res['data_fim']}* ({duracao_str})\n"
        f"• Odômetro: *{res['km_inicial']:,.1f} km* ➔ *{res['km_final']:,.1f} km*\n".replace(",", ".") +
        f"• Distância Rodada: *{km_rodados:,.1f} km*\n\n".replace(",", ".") +
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
        f"• Atingimento Meta Diária (R$ {meta_diaria:.2f}): *{perc_meta:.1f}%*\n\n"
        f"🛡️ *Cofre Contábil Atualizado! Fechamento registado com sucesso. Bom descanso!*"
    )

async def enviar_whatsapp(remote_jid: str, texto: str):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "text": texto}
            )
    except Exception as e:
        print(f"Erro WhatsApp: {e}")

@app.post("/webhooks/evolution")
async def evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        data = body.get("data", {})

        if data.get("key", {}).get("fromMe", False):
            return {"status": "ignored"}

        remote_jid = data.get("key", {}).get("remoteJid", "")
        tenant_id = remote_jid.split("@")[0] if remote_jid else "unknown"
        wpp_msg_id = data.get("key", {}).get("id", "unknown")

        texto_bruto = (
            data.get("message", {}).get("conversation") or
            data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
        ).strip()

        if not texto_bruto or tenant_id == "unknown":
            return {"status": "ignored"}

        texto_limpo = normalizar_texto(texto_bruto)
        motorista = await DatabaseService.buscar_motorista_por_telefone(tenant_id)

        if not motorista:
            background_tasks.add_task(enviar_whatsapp, remote_jid, "Fala, motorista! Registo não encontrado. Aciona o suporte para ser registado.")
            return {"status": "unregistered"}

        motorista_id = str(motorista["id"])
        fsm_turno_key = f"turno_flow:{tenant_id}"

        # 1. ESCAPE GLOBAL
        if any(t in texto_limpo for t in ["cancelar", "esquece", "abortar", "parar"]):
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            background_tasks.add_task(enviar_whatsapp, remote_jid, "✅ Ação cancelada com sucesso. O que faremos agora?")
            return {"status": "cancelled"}

        # 2. INTERCEÇÃO FSM ABSOLUTA (AGUARDANDO KM)
        estado_turno = await RedisFSMService.obter_estado(fsm_turno_key)
        if estado_turno in ["AGUARDANDO_KM_INICIAL", "AGUARDANDO_KM_FINAL"]:
            km_digitado = converter_para_float(texto_bruto)

            if km_digitado > 0:
                conn = await DatabaseService.obter_conexao()
                try:
                    veiculo = await conn.fetchrow("SELECT id FROM veiculos WHERE motorista_id = $1::uuid LIMIT 1;", motorista_id)
                finally:
                    await conn.close()

                if estado_turno == "AGUARDANDO_KM_INICIAL":
                    if veiculo:
                        res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_digitado)
                        if res["sucesso"]:
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                            resposta = f"🚀 Turno aberto! Odômetro: *{km_digitado:,.1f} km*. Boa jornada, {motorista['nome']}!".replace(",", ".")
                        else:
                            tipo_erro = res.get("tipo_erro", "")
                            if tipo_erro == "TURNO_JA_ATIVO":
                                await RedisFSMService.limpar_buffer(fsm_turno_key)
                            else:
                                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                            resposta = res['erro']
                    else:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        resposta = "⚠️ Nenhum veículo cadastrado na sua conta."

                elif estado_turno == "AGUARDANDO_KM_FINAL":
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_digitado)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res)
                    else:
                        tipo_erro = res.get("tipo_erro", "")
                        if tipo_erro != "ODOMETRO_DIVERGENTE":
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                        else:
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                        resposta = res['erro']

                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
                return {"status": "fsm_km_processed"}
            else:
                background_tasks.add_task(enviar_whatsapp, remote_jid, "⚠️ Por favor, envia apenas número válido da quilometragem atual do painel (ex: 1399):")
                return {"status": "fsm_waiting_km"}

        # 3. INTENÇÕES DE GESTÃO DE JORNADA
        tem_intencao_inicio = any(w in texto_limpo for w in ["iniciar", "comecar", "abrir", "bora", "turno", "inicio"])
        tem_intencao_fim = any(w in texto_limpo for w in ["encerrar", "fechar", "finalizar", "terminar", "fim"])

        if tem_intencao_inicio or tem_intencao_fim:
            await RedisFSMService.limpar_buffer(fsm_turno_key)

        numeros = re.findall(r'\d+', texto_limpo.replace('.', '').replace(',', ''))
        km_encontrado = next((float(num) for num in numeros if float(num) > 100), None)

        if tem_intencao_inicio:
            if km_encontrado:
                conn = await DatabaseService.obter_conexao()
                try:
                    veiculo = await conn.fetchrow("SELECT id FROM veiculos WHERE motorista_id = $1::uuid LIMIT 1;", motorista_id)
                finally:
                    await conn.close()

                if veiculo:
                    res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_encontrado)
                    resposta = f"🚀 Turno aberto! Odômetro: *{km_encontrado:,.1f} km*. Boa jornada, {motorista['nome']}!".replace(",", ".") if res["sucesso"] else res['erro']
                else:
                    resposta = "⚠️ Nenhum veículo cadastrado na sua conta."
                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                background_tasks.add_task(enviar_whatsapp, remote_jid, "🟢 Beleza! Qual é a *quilometragem atual* do painel? (Ex: 1399)")
            return {"status": "turno_start_intent"}

        if tem_intencao_fim:
            if km_encontrado:
                res = await TurnoService.fechar_turno_com_dre(motorista_id, km_encontrado)
                resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res) if res["sucesso"] else res['erro']
                background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                background_tasks.add_task(enviar_whatsapp, remote_jid, "🏁 Para gerar o seu DRE diário, qual é a *quilometragem final* do painel?")
            return {"status": "turno_end_intent"}

        # 4. INTENÇÕES DE PAUSA, RETOMA E STATUS (Nova Lógica Implementada)
        tem_intencao_pausa = any(w in texto_limpo for w in ["pausar", "pausa"])
        tem_intencao_retomar = any(w in texto_limpo for w in ["retomar", "voltar"])
        tem_intencao_status = any(w in texto_limpo for w in ["status", "resumo"])

        if tem_intencao_pausa:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            res = await TurnoService.pausar_turno(motorista_id)
            if res.get("sucesso"):
                resposta = res.get("mensagem", "⏸️ Turno pausado. Bom descanso!")
            else:
                resposta = res.get("erro", "⚠️ Não foi possível pausar o turno.")
            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "turno_paused"}

        if tem_intencao_retomar:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            res = await TurnoService.retomar_turno(motorista_id)
            if res.get("sucesso"):
                resposta = res.get("mensagem", "▶️ Turno retomado! Bora faturar!")
            else:
                resposta = res.get("erro", "⚠️ Não foi possível retomar o turno.")
            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "turno_resumed"}

        if tem_intencao_status:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            res = await TurnoService.obter_status_turno(motorista_id)
            if res.get("sucesso"):
                resposta = res.get("mensagem", "📊 Status do turno processado com sucesso.")
            else:
                resposta = res.get("erro", "⚠️ Não foi possível obter o status atual.")
            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "turno_status"}

        # 5. LANÇAMENTOS FINANCEIROS LIVRES (Blindado com Parser Monetário Seguro)
        palavras_chave_financeiras = [
            'recebi', 'ganhei', 'faturei', 'corrida', 'uber', '99', 
            'gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 
            'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado'
        ]
        is_financeiro = any(w in texto_limpo for w in palavras_chave_financeiras)
        valor_transacao = converter_para_float(texto_bruto)

        if is_financeiro and valor_transacao > 0:
            is_despesa = any(
                w in texto_limpo for w in [
                    'gastei', 'paguei', 'despesa', 'combustiv', 
                    'gasolin', 'posto', 'almoco', 'bala', 'lava', 
                    'marmita', 'mercado'
                ]
            )
            if is_despesa:
                cat = 'combustivel' if any(c in texto_limpo for c in ['gasolin', 'posto', 'combustiv']) else 'geral'
                await TransacaoService.registrar_transacao(motorista_id, 'despesa', cat, valor_transacao, texto_bruto, wpp_msg_id)
                resposta = f"✅ Despesa de *R$ {valor_transacao:,.2f}* registada com sucesso! 🛡️".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                await TransacaoService.registrar_transacao(motorista_id, 'receita', 'corrida', valor_transacao, texto_bruto, wpp_msg_id)
                resposta = f"✅ Receita de *R$ {valor_transacao:,.2f}* guardada no cofre! 🛡️".replace(",", "X").replace(".", ",").replace("X", ".")

            background_tasks.add_task(enviar_whatsapp, remote_jid, resposta)
            return {"status": "finance_logged"}

        # 6. CATCH-ALL
        resposta_ajuda = (
            "🤖 Não reconheci a ação! Aqui tens os comandos rápidos:\n\n"
            "🟢 *Iniciar* (ou 'iniciar 1399')\n"
            "🏁 *Fechar* (ou 'fechar 1450')\n"
            "⏸️ *Pausar* / *Retomar*\n"
            "📊 *Status* (resumo do dia)\n"
            "💰 *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')\n\n"
            "Como posso ajudar agora?"
        )
        background_tasks.add_task(enviar_whatsapp, remote_jid, resposta_ajuda)
        return {"status": "received"}

    except Exception as e:
        print(f"Erro crítico no Webhook: {e}")
        return {"status": "error", "detail": str(e)}
