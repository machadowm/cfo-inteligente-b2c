import os
import re
import unicodedata
import logging
import json
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from decimal import Decimal, ROUND_HALF_UP

# Bibliotecas de Terceiros
import httpx
from fastapi import FastAPI, Request, BackgroundTasks, status

# Imports de Serviços Locais (Arquitetura Modular v3)
# Conforme diretriz de arquitetura, main.py delega a lógica de negócio 
# para o OrchestratorService, mantendo o acoplamento mínimo.
from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.orchestrator_service import OrchestratorService

# ==============================================================================
# CONFIGURAÇÕES GLOBAIS E OBSERVABILIDADE
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configurações da Evolution API com fallbacks de segurança
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")
APP_VERSION = "3.0.0-modular"

# ==============================================================================
# GERENCIAMENTO DE CICLO DE VIDA (LIFESPAN)
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gerencia a inicialização e o encerramento gracioso dos recursos do sistema.
    Garante a persistência do pool de conexões e encerramento de sockets.
    """
    logger.info(f"Initializing CFO Inteligente B2C Backend v{APP_VERSION}...")
    
    # Inicialização do pool de conexões do PostgreSQL (asyncpg)
    await DatabaseService.initialize_pool()
    
    yield
    
    logger.info("Iniciando shutdown gracioso (Draining connections)...")
    
    # Encerramento do pool do PostgreSQL
    await DatabaseService.close_pool()
    
    # Encerramento da conexão com o Redis
    try:
        redis_client = await RedisFSMService.get_client()
        await redis_client.aclose()
        logger.info("Conexão Redis encerrada com sucesso.")
    except Exception as e:
        logger.error(f"Falha ao fechar conexão com Redis: {e}")

# ==============================================================================
# INICIALIZAÇÃO DA INSTÂNCIA FASTAPI
# ==============================================================================

app = FastAPI(
    title="CFO Inteligente B2C API",
    description="SaaS financeiro e ERP logístico conversacional de fricção zero via WhatsApp",
    version=APP_VERSION,
    lifespan=lifespan
)

# ==============================================================================
# FUNÇÕES UTILITÁRIAS DE TRATAMENTO DE DADOS
# ==============================================================================

def normalizar_texto(texto: str) -> str:
    """
    Sanitiza strings removendo acentos e caracteres especiais para reconhecimento de intenção.
    """
    if not texto:
        return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r'[^a-zA-Z0-9\s]', '', sem_acento).lower().strip()

def converter_para_float(texto_valor: str) -> float:
    """
    Parser monetário robusto resiliente a padrões brasileiros (R$).
    """
    if not texto_valor:
        return 0.0
    try:
        limpo = texto_valor.upper().replace("R$", "").replace("REAIS", "").strip()
        limpo = re.sub(r'[^\d.,]', '', limpo)
        
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
    except (ValueError, TypeError):
        return 0.0

def formatar_moeda_br(valor: float) -> str:
    """
    Formata valores numéricos para o padrão de moeda brasileiro (Bank-Grade).
    """
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ==============================================================================
# HELPERS DE FORMATAÇÃO DE INTERFACE CONVERSACIONAL (UI/UX WHATSAPP)
# ==============================================================================

def formatar_relatorio_parcial(nome_motorista: str, info: Dict[str, Any]) -> str:
    """
    Template para status do turno em andamento, incluindo métricas de escala e metas.
    """
    aluguel_diario = info["custo_aluguel_semanal"] / 6.0
    franquia_diaria = info["franquia_km_semanal"] / 7.0
    meta_diaria = info["meta_mensal"] / info["dias_uteis"]
    
    km_formatado = f"{info['km_inicial']:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    return (
        f"📥 *DADOS PARCIAIS DO TURNO DE HOJE ({info['data_turno']})*\n\n"
        f"• Início: *{info['data_inicio_hora']}*\n"
        f"• KM Inicial: *{km_formatado} km*\n"
        f"• Combustível Lançado: *R$ {formatar_moeda_br(info['total_abastecido'])}*\n\n"
        f"⚙ *CUSTOS DO CONTRATO ({info['locadora']})*\n\n"
        f"• Escala: *{info['escala_trabalho']}*\n"
        f"• Aluguel Diário: *R$ {formatar_moeda_br(aluguel_diario)}* (semanal R$ {formatar_moeda_br(info['custo_aluguel_semanal'])} / 6d)\n"
        f"• Franquia Recomendada: *{franquia_diaria:.0f} km/dia*\n"
        f"• KM Excedente: *R$ {formatar_moeda_br(info['valor_km_excedente'])}/km*\n\n"
        f"🎯 *METAS DE HOJE*\n\n"
        f"• Meta Diária: *R$ {formatar_moeda_br(meta_diaria)}*\n"
        f"• Piso Mínimo Indicado: *R$ 2,00/km* | *R$ 30,00/h*\n\n"
        f"🔮 Para encerrar a sua jornada e emitir o DRE, envie: *'fechar [KM final]'*"
    )

def formatar_relatorio_fechamento_dre(nome_motorista: str, res: Dict[str, Any]) -> str:
    """
    Template para o DRE Executivo Diário consolidado com indicadores de performance exaustivos.
    """
    # Cálculos de tempo
    horas = int(res["tempo_total_min"] // 60)
    minutos = int(res["tempo_total_min"] % 60)
    duracao_str = f"{horas}h {minutos}min" if horas > 0 else f"{minutos} min"
    
    # Indicadores Base
    km_rodados = res["km_rodados"] if res["km_rodados"] > 0 else 1.0
    horas_trab = res.get("horas_trabalhadas", 1.0)
    fat = res["faturamento_bruto"]
    c_var = res["custo_variavel"]
    c_fixo = res["custo_fixo_rateado"]
    lucro = res["lucro_liquido_real"]
    
    # Indicadores de Performance (Source Context Line 131+)
    faturamento_por_km = fat / km_rodados
    faturamento_por_hora = fat / horas_trab
    lucro_por_hora = lucro / horas_trab
    margem_lucro = (lucro / fat * 100.0) if fat > 0 else 0.0
    meta_diaria = res.get("meta_diaria", (res.get("meta_mensal", 12000) / res.get("dias_uteis", 26)))
    perc_meta = (fat / meta_diaria * 100.0) if meta_diaria > 0 else 0.0
    
    # Detalhamento de Despesas (Source Line 139+)
    lista_despesas_str = ""
    despesas = res.get("despesas_detalhadas", [])
    if despesas:
        lista_despesas_str = "• Detalhes dos Gastos:\n"
        for d in despesas:
            desc = d.get('descricao_original') or d.get('categoria', 'geral')
            val = float(d.get('valor', 0.0))
            lista_despesas_str += f" - *{desc}*: R$ {formatar_moeda_br(val)}\n"
    else:
        lista_despesas_str = "• Nenhuma despesa registrada neste turno.\n"

    # Lógica de Sugestão de Contrato (Source Line 144+)
    rodape_sugestao = ""
    if not res.get("contrato_personalizado", False):
        rodape_sugestao = (
            "\n\n"
            f"{nome_motorista.split()[0]}, este cálculo usou custos padrão. Para lucro 100% real, ajuste seu contrato:\n\n"
            "1⃣ *Carro Alugado* (Zarp, Movida, etc):\n"
            "👉 'atualizar contrato [Locadora] [Valor Semanal] [Franquia KM]'\n\n"
            "2⃣ *Carro Próprio* (Manutenção Diária):\n"
            "👉 'atualizar contrato Proprietario [Valor Diário] 0'\n\n"
            "3⃣ *Carro Financiado* (Mensalidade + Manut):\n"
            "👉 'atualizar contrato Financiado [Pro-Rata Diário] 0'"
        )

    km_ini_fmt = f"{res['km_inicial']:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    km_fim_fmt = f"{res['km_final']:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    km_rod_fmt = f"{km_rodados:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")

    return (
        f"🏁 *FECHAMENTO DE TURNO - DRE EXECUTIVO DIÁRIO*\n"
        f"👤 Motorista: *{nome_motorista}*\n"
        f"──────────────────────────────\n\n"
        f"⏱ *1. RESUMO OPERACIONAL*\n"
        f"• Horário: *{res['data_inicio']}* às *{res['data_fim']}* ({duracao_str})\n"
        f"• Odômetro: *{km_ini_fmt} km* ➔ *{km_fim_fmt} km*\n"
        f"• Distância Rodada: *{km_rod_fmt} km*\n\n"
        f"📊 *2. DEMONSTRATIVO DE RESULTADO (DRE)*\n"
        f"• (+) Faturamento Bruto: *R$ {formatar_moeda_br(fat)}*\n"
        f"• (-) Custos Variáveis:\n{lista_despesas_str}"
        f"  *Total Custos Variáveis: R$ {formatar_moeda_br(c_var)}*\n"
        f"• (=) Margem Contribuição: *R$ {formatar_moeda_br(fat - c_var)}*\n"
        f"• (-) Rateio Custo Fixo: *R$ {formatar_moeda_br(c_fixo)}*\n"
        f"──────────────────────────────\n"
        f"💰 *LUCRO LÍQUIDO REAL: R$ {formatar_moeda_br(lucro)}*\n"
        f"📈 Margem Líquida Real: *{margem_lucro:.1f}%*\n\n"
        f"🎯 *3. INDICADORES DE PERFORMANCE*\n"
        f"• Ganho por KM: *R$ {formatar_moeda_br(faturamento_por_km)}/km*\n"
        f"• Ganho por Hora: *R$ {formatar_moeda_br(faturamento_por_hora)}/h*\n"
        f"• Lucro Real por Hora: *R$ {formatar_moeda_br(lucro_por_hora)}/h*\n"
        f"• Rendimento Médio: *{res.get('km_por_litro', 0.0):.2f} km/L*\n"
        f"• Atingimento Meta Diária: *{perc_meta:.1f}%*\n\n"
        f"🛡 *Cofre Contábil Atualizado com Sucesso.*"
        f"{rodape_sugestao}"
    )

# ==============================================================================
# CAMADA DE INTEGRAÇÃO DE SAÍDA (OUTBOUND MESSAGING)
# ==============================================================================

async def enviar_whatsapp(remote_jid: str, texto: str) -> None:
    """
    Envia uma mensagem de texto via POST para a Evolution API.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "text": texto},
                timeout=12.0
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"[Evolution API] Falha crítica de envio para {remote_jid}: {e}")

async def enviar_status_digitando(remote_jid: str) -> None:
    """
    Aciona o status 'composing' (digitando...) para aumentar a retenção do usuário.
    """
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{EVOLUTION_API_URL}/chat/sendPresence/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "presence": "composing", "delay": 2500},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"[Evolution API] Falha ao sinalizar presença: {e}")

# ==============================================================================
# LÓGICA DE RESILIÊNCIA E ESCAPE DE FSM
# ==============================================================================

async def registrar_erro_e_verificar_escape(remote_jid: str, tenant_id: str, fsm_key: str, msg_erro: str) -> bool:
    """
    Monitora falhas consecutivas. Ao atingir o threshold de 3 erros, reinicia o estado
    do usuário e fornece comandos rápidos de escape.
    """
    erros = await RedisFSMService.incrementar_erros_consecutivos(tenant_id)
    if erros >= 3:
        await RedisFSMService.limpar_buffer(fsm_key)
        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
        
        mensagem_escape = (
            "⚠ *Múltiplos Erros Consecutivos!*\n"
            "Seu fluxo atual foi reiniciado para sua segurança.\n\n"
            "Comandos rápidos:\n"
            "🟢 *Iniciar* (ex: 'iniciar 1399')\n"
            "🏁 *Fechar* (ex: 'fechar 1450')\n"
            "⏸ *Pausar* / *Retomar*\n"
            "📊 *Status* (resumo do dia)\n"
            "💰 *[Valor]* (ex: 'ganhei 100')"
        )
        await enviar_whatsapp(remote_jid, mensagem_escape)
        return True
    
    await enviar_whatsapp(remote_jid, msg_erro)
    return False

# ==============================================================================
# ENDPOINTS DE VERIFICAÇÃO DE SAÚDE (OBSERVABILIDADE)
# ==============================================================================

@app.get("/health")
async def health_check() -> Dict[str, str]:
    """
    Health check dinâmico para monitoramento SRE.
    """
    return {
        "status": "healthy", 
        "engine": "FastAPI & PostgreSQL 15 Bank-Grade",
        "version": APP_VERSION,
        "uptime_logic": "active"
    }

# ==============================================================================
# ROTEADOR DE INGESTÃO DE WEBHOOKS (THE ENGINE)
# ==============================================================================

@app.post("/webhooks/evolution")
@app.post("/webhook/evolution")
@app.post("/api/v1/webhook/whatsapp")
@app.post("/webhook/whatsapp")
async def evolution_webhook_routing(request: Request, background_tasks: BackgroundTasks):
    """
    Ponto de entrada unificado para webhooks. Implementa parsing robusto
    e delega a orquestração para a camada de serviços modularizada.
    """
    try:
        # Parsing Robusto (Source Line 377)
        try:
            body = await request.json()
        except Exception as e:
            logger.error(f"Payload JSON inválido recebido: {e}")
            return {"status": "error", "message": "Invalid JSON"}

        data = body.get("data", {})
        
        # Blindagem contra auto-looping (Mensagens enviadas pelo próprio bot)
        if data.get("key", {}).get("fromMe", False):
            return {"status": "ignored", "reason": "self_message"}

        remote_jid = data.get("key", {}).get("remoteJid", "")
        tenant_id = remote_jid.split("@")[0] if remote_jid else "unknown"
        wpp_msg_id = data.get("key", {}).get("id", "unknown")
        
        # Extração polimórfica de conteúdo textual
        texto_bruto = (
            data.get("message", {}).get("conversation") or 
            data.get("message", {}).get("extendedTextMessage", {}).get("text") or 
            data.get("message", {}).get("imageMessage", {}).get("caption") or ""
        ).strip()

        if not texto_bruto or tenant_id == "unknown":
            return {"status": "ignored", "reason": "empty_content_or_tenant"}

        # Feedback visual imediato
        background_tasks.add_task(enviar_status_digitando, remote_jid)

        # Delegação para o Orquestrador (Single Point of Truth para lógica de negócio)
        await OrchestratorService.router(
            tenant_id=tenant_id,
            remote_jid=remote_jid,
            texto_bruto=texto_bruto,
            wpp_id=wpp_msg_id
        )

        return {"status": "processed", "id": wpp_msg_id}

    except Exception as e:
        logger.exception(f"Erro crítico no processamento do webhook: {e}")
        return {"status": "error", "detail": "Internal server error during ingestion"}

# ==============================================================================
# BLOCO DE EXECUÇÃO PRINCIPAL
# ==============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True if os.getenv("ENV") == "dev" else False,
        workers=int(os.getenv("WEB_CONCURRENCY", 1))
    )
