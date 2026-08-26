import os
import re
import json
import httpx
import logging
import unicodedata
from datetime import datetime
from typing import Optional, Dict, Any, List

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.transacao_service import TransacaoService
from services.turno_service import TurnoService
from services.help_service import HelpService

# Configuração de Logger
logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

# --- Funções Auxiliares de Formatação e Utilitários ---

def normalizar_texto(texto: str) -> str:
    """Sanitiza strings removendo acentos e pontuações para reconhecimento de intenção."""
    if not texto: return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r'[^a-zA-Z0-9\s]', '', sem_acento).lower().strip()

def converter_para_float(texto_valor: str) -> float:
    """Parser monetário robusto e resiliente a padrões regionais brasileiros (ex: R$ 1.500,50 -> 1500.50)."""
    try:
        limpo = texto_valor.upper().replace("R$", "").replace("REAIS", "").strip()
        limpo = re.sub(r'[^\d.,]', '', limpo)
        if "," in limpo and "." in limpo:
            if limpo.find(".") < limpo.find(","):  # Padrão brasileiro: 1.000,50
                limpo = limpo.replace(".", "").replace(",", ".")
            else:  # Padrão americano: 1,000.50
                limpo = limpo.replace(",", "")
        elif "," in limpo:
            limpo = limpo.replace(",", ".")
        return float(limpo)
    except ValueError:
        return 0.0

def formatar_relatorio_parcial(nome_motorista: str, info: dict) -> str:
    """Gera o status parcial do turno atualizado com as regras contratuais e do DRE."""
    aluguel_diario = info["custo_aluguel_semanal"] / 6.0
    franquia_diaria = info["franquia_km_semanal"] / 7.0
    meta_diaria = info["meta_mensal"] / info["dias_uteis"]
    return (
        f"📥  *DADOS PARCIAIS DO TURNO DE HOJE ({info['data_turno']})* \n\n"
        f"• Início:  *{info['data_inicio_hora']}* \n"
        f"• KM Inicial:  *{info['km_inicial']:,.1f} km* \n".replace(",", ".") +
        f"• Combustível Lançado:  *R$ {info['total_abastecido']:.2f}* \n\n"
        f"⚙  *CUSTOS DO CONTRATO ({info['locadora']})* \n\n"
        f"• Escala:  *{info['escala_trabalho']}* \n"
        f"• Aluguel Diário:  *R$ {aluguel_diario:.2f}*  (semanal R$ {info['custo_aluguel_semanal']:.2f} / 6d)\n"
        f"• Franquia Recomendada:  *{franquia_diaria:.0f} km/dia* \n"
        f"• KM Excedente:  *R$ {info['valor_km_excedente']:.2f}/km* \n\n"
        f"🎯  *METAS DE HOJE* \n\n"
        f"• Meta Diária:  *R$ {meta_diaria:.2f}* \n"
        f"• Piso Mínimo Indicado:  *R$ 2,00/km*  |  *R$ 30,00/h* \n\n"
        f"🔮 Para encerrar a sua jornada e emitir o DRE, envie:  *'fechar [KM final]'"
    )

def formatar_relatorio_fechamento_dre(nome_motorista: str, res: dict) -> str:
    """Formata o DRE Executivo Diário consolidando o tempo operacional e indicadores."""
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
            lista_despesas_str += f" -  *{desc}* :  *R$ {val:.2f}* \n"
    else:
        lista_despesas_str = "• Nenhuma despesa registrada neste turno.\n"
        
    rodape_sugestao = ""
    if not res.get("contrato_personalizado", False):
        rodape_sugestao = (
            "\n\n"
            "Willian, este cálculo de hoje usou o custo padrão de  *Aluguel da Zarp (R$ 170,14/dia)* . "
            "But we want your profit to be 100% real. Como você trabalha nas ruas hoje? "
            "Escolha uma opção para ajustarmos o bot ao seu bolso:\n\n"
            "1⃣  *Carro Alugado*  (Zarp, Movida, Mottu, etc.):\n"
            "👉 Envie:  *'atualizar contrato [Locadora] [Aluguel Semanal] [Franquia KM]'*  (ex:  *atualizar contrato Zarp 1020 1500* )\n\n"
            "2⃣  *Carro Próprio Quitado*  (sem mensalidade, apenas provisão de manutenção):\n"
            "👉 Envie:  *'atualizar contrato Proprietario [Manutenção Diária] 0'*  (ex:  *atualizar contrato Proprietario 15 0* )\n\n"
            "3⃣  *Carro Financiado*  (mensalidade + manutenção):\n"
            "👉 Envie:  *'atualizar contrato Financiado [Pro-Rata Mensalidade + Manutenção] 0'*  (ex:  *atualizar contrato Financiado 45 0* )"
        )
    return (
        f"🏁  *FECHAMENTO DE TURNO - DRE EXECUTIVO DIÁRIO* \n"
        f"👤 Motorista:  *{nome_motorista}* \n"
        f"──────────────────────────────\n\n"
        f"⏱  *1. RESUMO OPERACIONAL* \n"
        f"• Horário:  *{res['data_inicio']}*  às  *{res['data_fim']}*  ({duracao_str})\n"
        f"• Odômetro:  *{res['km_inicial']:,.1f} km*  ➔  *{res['km_final']:,.1f} km* \n".replace(",", ".") +
        f"• Distância Rodada:  *{km_rodados:,.1f} km* \n\n".replace(",", ".") +
        f"📊  *2. DEMONSTRATIVO DE RESULTADO (DRE)* \n"
        f"• (+) Faturamento Bruto:  *R$ {fat:.2f}* \n"
        f"• (-) Custos Variáveis:\n"
        f"{lista_despesas_str}"
        f"  *Total Custos Variáveis: R$ {c_var:.2f}* \n"
        f"• (=) Margem Contribuição:  *R$ {(fat - c_var):.2f}* \n"
        f"• (-) Rateio Custo Fixo (Aluguel/Pro-Rata):  *R$ {c_fixo:.2f}* \n"
        f"──────────────────────────────\n"
        f"💰  *LUCRO LÍQUIDO REAL DO DIA: R$ {lucro:.2f}* \n"
        f"📈 Margem Líquida Real:  *{margem_lucro:.1f}%* \n\n"
        f"🎯  *3. INDICADORES DE PERFORMANCE* \n"
        f"• Faturamento por KM:  *R$ {faturamento_por_km:.2f}/km* \n"
        f"• Faturamento por Hora:  *R$ {faturamento_por_hora:.2f}/h* \n"
        f"• Lucro Real por Hora:  *R$ {lucro_por_hora:.2f}/h* \n"
        f"• Rendimento Médio do Turno:  *{res.get('km_por_litro', 0.0):.2f} km/L* \n"
        f"• Atingimento Meta Diária (R$ {meta_diaria:.2f}):  *{perc_meta:.1f}%* \n\n"
        f"🛡  *Cofre Contábil Atualizado! Fechamento registrado com sucesso. Bom descanso!*"
        f"{rodape_sugestao}"
    )

async def enviar_whatsapp(remote_jid: str, texto: str):
    """Envia uma mensagem de texto de volta ao WhatsApp usando o gateway Evolution API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "text": texto},
                timeout=10.0
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"[Evolution API] Falha ao enviar mensagem de texto para {remote_jid}: {e}")

async def enviar_status_digitando(remote_jid: str):
    """Sinaliza no WhatsApp que o chatbot está escrevendo (Presence composing) para reter atenção."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{EVOLUTION_API_URL}/chat/sendPresence/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "presence": "composing", "delay": 2000},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"[Evolution API] Falha ao simular presença para {remote_jid}: {e}")

async def registrar_erro_e_verificar_escape(remote_jid: str, tenant_id: str, fsm_key: str, msg_erro: str) -> bool:
    """Registra erro consecutivo no Redis e força o escape se atingir 3 falhas."""
    erros = await RedisFSMService.incrementar_erros_consecutivos(tenant_id)
    if erros >= 3:
        await RedisFSMService.limpar_buffer(fsm_key)
        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
        mensagem_escape = (
            "⚠  *Múltiplos Erros Consecutivos!* \n"
            "Seu fluxo atual foi interrompido e limpo para evitar travamento.\n\n"
            "Por favor, envie um dos comandos rápidos ou valores livres para começar de novo:\n"
            "🟢  *Iniciar*  (ou 'iniciar 1399')\n"
            "🏁  *Fechar*  (ou 'fechar 1450')\n"
            "⏸  *Pausar*  /  *Retomar* \n"
            "📊  *Status*  (resumo do dia)\n"
            "💰  *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')"
        )
        await enviar_whatsapp(remote_jid, mensagem_escape)
        return True
    await enviar_whatsapp(remote_jid, msg_erro)
    return False

# --- Orquestrador de Mensagens ---

class OrchestratorService:
    # Janela de debouncing em segundos. Mensagens enviadas em rajada dentro
    # desta janela são bufferizadas e processadas como um único evento.
    _DEBOUNCE_JANELA_SEGUNDOS: int = 4

    @staticmethod
    async def router(tenant_id: str, remote_jid: str, texto_bruto: str, wpp_msg_id: Optional[str] = None, **kwargs):
        """
        Orquestrador Central do Fluxo Contábil e de Onboarding.
        Remove 100% da sobrecarga do FastAPI Event Loop rodando de forma assíncrona.
        Implementa debouncing de mensagens via Redis para aglutinação de rajadas.
        """
        # Resiliência de Interface: Tolera chamadas legadas com nomes de parâmetro variáveis
        wpp_msg_id = wpp_msg_id or kwargs.get("wpp_id") or kwargs.get("wpp_msg_id") or "unknown"

        # --- DEBOUNCING: Acumula a mensagem no buffer e aguarda a janela fechar ---
        mensagens_buffer = await RedisFSMService.acumular_mensagem(
            tenant_id, texto_bruto, OrchestratorService._DEBOUNCE_JANELA_SEGUNDOS
        )
        # Se há mais de uma mensagem no buffer, esta não é a última da rajada:
        # descarta silenciosamente e aguarda o processamento da mensagem final.
        if len(mensagens_buffer) > 1:
            return

        # Reconstrói o texto consolidado caso múltiplas mensagens tenham chegado
        # e esta seja a última (o buffer já terá expirado para as anteriores).
        # Na prática com janela de 4s, chegamos aqui com exatamente 1 mensagem.
        texto_bruto = " ".join(mensagens_buffer) if mensagens_buffer else texto_bruto

        texto_limpo = normalizar_texto(texto_bruto)

        # 1. Bypass de Cache de Perfil no Redis para Velocidade Absoluta
        motorista = await RedisFSMService.obter_perfil_cache(tenant_id)
        if not motorista:
            motorista_record = await DatabaseService.buscar_motorista_por_telefone(tenant_id)
            if motorista_record:
                motorista = dict(motorista_record)
                await RedisFSMService.cache_perfil_motorista(tenant_id, motorista)

        # =========================================================================
        # FLUXO DE ONBOARDING CONVERSACIONAL (FRICÇÃO ZERO)
        # =========================================================================
        if not motorista:
            fsm_key = f"onboard:{tenant_id}"
            estado_atual = await RedisFSMService.obter_estado(fsm_key)

            if estado_atual == "IDLE" or not estado_atual:
                await RedisFSMService.definir_estado(fsm_key, "AGUARDANDO_NOME")
                await enviar_whatsapp(
                    remote_jid,
                    "Fala, motorista! Seja bem-vindo ao  *CFO Inteligente B2C*  🚀\n"
                    "Percebi que você ainda não tem cadastro por aqui. Vamos resolver isso em 1 minuto de forma simples!\n\n"
                    "Para começar, digite o seu  **nome completo** :"
                )
                return

            elif estado_atual == "AGUARDANDO_NOME":
                if len(texto_bruto) < 3:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "⚠ O seu nome deve conter pelo menos 3 caracteres. Digite novamente:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_VEICULO|name:{texto_bruto}")
                await enviar_whatsapp(
                    remote_jid, f"Prazer em te conhecer,  *{texto_bruto}* ! 🚗\nQual é o  **modelo e marca**  do seu principal veículo de trabalho?"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_VEICULO"):
                nome = estado_atual.split("|name:")[1] if "|name:" in estado_atual else "Motorista"
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_COMBUSTIVEL|name:{nome}|veiculo:{texto_bruto}")
                await enviar_whatsapp(
                    remote_jid,
                    f"Show! E qual é o tipo de combustível ou motorização do seu {texto_bruto}? ⛽\n\n"
                    "Responda exatamente com uma das opções:\n"
                    "👉  *Gasolina* \n"
                    "👉  *Etanol* \n"
                    "👉  *Flex* \n"
                    "👉  *Hibrido* \n"
                    "👉  *Eletrico* \n"
                    "👉  *GNV*"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_COMBUSTIVEL"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                comb_input = texto_bruto.lower().replace("é", "e").replace("í", "i").strip()
                combustiveis_suportados = ["gasolina", "etanol", "flex", "hibrido", "eletrico", "gnv"]
                if comb_input not in combustiveis_suportados:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "⚠ Por favor, responda exatamente com um dos combustíveis suportados:  *Gasolina, Etanol, Flex, Hibrido, Eletrico*  ou  *GNV* :"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_PLACA|name:{nome}|veiculo:{veiculo}|combustivel:{comb_input}")
                await enviar_whatsapp(
                    remote_jid, f"Perfeito! E qual é a  **placa do seu veículo** ? (Ex: ABC-1234 ou ABC1D23)"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_PLACA"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                combustivel = partes[3].split("combustivel:")[1]
                placa_limpa = re.sub(r'[^A-Za-z0-9]', '', texto_bruto).upper()
                if len(placa_limpa) != 7:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "⚠ A placa do veículo deve conter exatamente 7 caracteres alfanuméricos. Digite novamente:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_CAPACIDADE_TANQUE|name:{nome}|veiculo:{veiculo}|combustivel:{combustivel}|placa:{placa_limpa}")
                await enviar_whatsapp(
                    remote_jid, "Legal! Agora me diz qual a  **capacidade máxima do tanque**  em litros do seu veículo? (Se for elétrico puro, responda  *0* ):"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_CAPACIDADE_TANQUE"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                combustivel = partes[3].split("combustivel:")[1]
                placa = partes[4].split("placa:")[1]
                tanque_val = converter_para_float(texto_bruto)
                if tanque_val < 0:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "⚠ A capacidade não pode ser negativa. Digite novamente:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                if combustivel in ["hibrido", "eletrico"]:
                    await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_CAPACIDADE_BATERIA|name:{nome}|veiculo:{veiculo}|combustivel:{combustivel}|placa:{placa}|tanque:{tanque_val}")
                    await enviar_whatsapp(remote_jid, "E qual a  **capacidade da bateria**  em kWh do seu veículo? (Ex: 30):")
                    return
                else:
                    await enviar_whatsapp(remote_jid, "⚙  *A preparar o seu cofre contábil... Só um segundo!*")
                    try:
                        motorista_uuid = await DatabaseService.registrar_novo_motorista(
                            telefone=tenant_id, nome=nome, veiculo_modelo=veiculo, combustivel=combustivel, placa=placa
                        )
                        async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                            if combustivel == "gnv":
                                estoque_dict = {"gnv": {"m3": 0.0, "custo_total": 0.0, "km_unidade": 14.0}}
                            else:
                                estoque_dict = {
                                    "liquido": {
                                        "litros": 0.0,
                                        "custo_total": 0.0,
                                        "gasolina_litros": 0.0,
                                        "etanol_litros": 0.0,
                                        "gasolina_proporcao": 1.0,
                                        "etanol_proporcao": 0.0,
                                        "km_l_gasolina": 12.0,
                                        "km_l_etanol": 8.5
                                    }
                                }
                            estoque_dict = {
                                "meta": {
                                    "tipo_veiculo": combustivel,
                                    "is_flex": bool(combustivel == "flex"),
                                    "is_hibrido": False,
                                    "is_eletrico": False,
                                    "capacidade_tanque_l": float(tanque_val),
                                    "capacidade_bateria_kwh": 0.0,
                                    "qtd_tanques": 1
                                },
                                "liquido": {
                                    "litros": 0.0,
                                    "custo_total": 0.0,
                                    "gasolina_litros": 0.0,
                                    "etanol_litros": 0.0,
                                    "gasolina_proporcao": 1.0,
                                    "etanol_proporcao": 0.0,
                                    "km_l_gasolina": 12.0,
                                    "km_l_etanol": 8.5
                                },
                                "eletricidade": {
                                    "kwh": 0.0,
                                    "custo_total": 0.0,
                                    "km_kwh": 6.5
                                },
                                "gnv": {
                                    "m3": 0.0,
                                    "custo_total": 0.0,
                                    "km_m3": 14.0
                                }
                            }
                            await conn.execute(
                                """
                                UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE motorista_id = $2::uuid AND ativo = TRUE;
                                """, json.dumps(estoque_dict), motorista_uuid
                            )
                        await RedisFSMService.limpar_buffer(fsm_key)
                        await enviar_whatsapp(
                            remote_jid, f"Cadastro concluído com sucesso, {nome}! 🛡\n"
                            f"O seu cofre contábil está ativo e configurado para o seu  *{veiculo}*  ({placa}).\n\n"
                            "Envie  *'Iniciar'*  ou  *'Iniciar turno'*  acompanhado do seu odômetro atual para começar!"
                        )
                    except Exception as e:
                        logger.error(f"Falha ao salvar onboarding no banco: {e}")
                        await RedisFSMService.limpar_buffer(fsm_key)
                    return

            elif estado_atual.startswith("AGUARDANDO_CAPACIDADE_BATERIA"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                combustivel = partes[3].split("combustivel:")[1]
                placa = partes[4].split("placa:")[1]
                tanque_val = float(partes[5].split("tanque:")[1])
                bateria_val = converter_para_float(texto_bruto)
                if bateria_val < 0:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "⚠ A capacidade não pode ser negativa. Digite novamente:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await enviar_whatsapp(remote_jid, "⚙  *A preparar o seu cofre contábil... Só um segundo!*")
                try:
                    motorista_uuid = await DatabaseService.registrar_novo_motorista(
                        telefone=tenant_id, nome=nome, veiculo_modelo=veiculo, combustivel=combustivel, placa=placa
                    )
                    async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                        estoque_dict = {
                            "meta": {
                                "tipo_veiculo": combustivel,
                                "is_flex": bool(combustivel == "flex"),
                                "is_hibrido": bool(combustivel == "hibrido"),
                                "is_eletrico": bool(combustivel == "eletrico"),
                                "capacidade_tanque_l": float(tanque_val),
                                "capacidade_bateria_kwh": float(bateria_val),
                                "qtd_tanques": 1
                            },
                            "liquido": {
                                "litros": 0.0,
                                "custo_total": 0.0,
                                "gasolina_litros": 0.0,
                                "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0,
                                "etanol_proporcao": 0.0,
                                "km_l_gasolina": 12.0,
                                "km_l_etanol": 8.5
                            },
                            "eletricidade": {
                                "kwh": 0.0,
                                "custo_total": 0.0,
                                "km_kwh": 6.5
                            },
                            "gnv": {
                                "m3": 0.0,
                                "custo_total": 0.0,
                                "km_m3": 14.0
                            }
                        }
                        await conn.execute(
                            """
                            UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE motorista_id = $2::uuid AND ativo = TRUE;
                            """, json.dumps(estoque_dict), motorista_uuid
                        )
                    await RedisFSMService.limpar_buffer(fsm_key)
                    await enviar_whatsapp(
                        remote_jid, f"Cadastro concluído com sucesso, {nome}! 🛡\n"
                        f"O seu cofre contábil está ativo e configurado para o seu  *{veiculo}*  ({placa}).\n\n"
                        "Envie  *'Iniciar'*  ou  *'Iniciar turno'* acompanhado do seu odômetro atual para começar!"
                    )
                except Exception as e:
                    logger.error(f"Falha ao salvar onboarding híbrido no banco: {e}")
                    await RedisFSMService.limpar_buffer(fsm_key)
                return

        # =========================================================================
        # MOTORISTAS CADASTRADOS: PROCESSO CONVERSACIONAL COM FSM DE TURNO
        # =========================================================================
        motorista_id = str(motorista["id"])
        fsm_turno_key = f"turno_flow:{tenant_id}"

        # AJUDA GLOBAL (Stateless)
        palavras_ajuda = ["ajuda", "help", "socorro", "como", "explicar"]
        if any(w in texto_limpo for w in palavras_ajuda):
            partes = texto_limpo.split()
            topico = "geral"
            for i, palavra in enumerate(partes):
                if palavra in palavras_ajuda and i + 1 < len(partes):
                    topico_potencial = partes[i+1]
                    if topico_potencial in ["metas", "contrato", "lancamentos", "turno"]:
                        topico = topico_potencial
                        break
            resposta_ajuda = HelpService.obter_ajuda(topico)
            await enviar_whatsapp(remote_jid, resposta_ajuda)
            return

        estado_turno = await RedisFSMService.obter_estado(fsm_turno_key)

        # Pre-parse de intenções
        tem_intencao_inicio = any(w in texto_limpo for w in ["iniciar", "comecar", "abrir", "bora", "turno", "inicio"])
        tem_intencao_fim = any(w in texto_limpo for w in ["encerrar", "fechar", "finalizar", "terminar", "fim"])
        tem_intencao_pausa = any(w in texto_limpo for w in ["pausa", "pausar", "pause", "pausei", "almocar"])
        tem_intencao_retomada = any(w in texto_limpo for w in ["retomar", "voltar", "voltei", "continuar", "retom"])
        is_status = any(t in texto_limpo for t in ['status', 'resumo', 'como estou', 'parcial', 'relatorio'])
        is_contrato = "atualizar contrato" in texto_limpo

        # 1. ATUALIZAÇÃO CONTRATUAL EM TEMPO REAL
        if is_contrato:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            partes = texto_bruto.split()
            try:
                locadora = partes[2] if len(partes) > 2 else "Localiza Zarp"
                aluguel_input = converter_para_float(partes[3]) if len(partes) > 3 else 1020.85
                franquia = converter_para_float(partes[4]) if len(partes) > 4 else 1505.00
                
                if locadora.lower() in ["proprietario", "quitado", "financiado"]:
                    aluguel_semanal = aluguel_input * 6.0
                else:
                    aluguel_semanal = aluguel_input
                    
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    await conn.execute(
                        """
                        UPDATE public.veiculos SET locadora = $1, custo_aluguel_semanal = $2, franquia_km_semanal = $3, contrato_personalizado = TRUE
                        WHERE motorista_id = $4::uuid AND ativo = TRUE;
                        """, locadora, aluguel_semanal, franquia, motorista_id
                    )
                await enviar_whatsapp(remote_jid, f"✅ Contrato atualizado com sucesso para  *{locadora}*! Aluguel rateado recalculado e cofre adaptado. 🛡")
            except Exception as e:
                logger.error(f"Erro ao atualizar contrato: {e}")
                await enviar_whatsapp(remote_jid, "⚠ Formato inválido. Use ex: *'atualizar contrato Zarp 1020.85 1505' *")
            return

        # 2. EMISSÃO DE REPORT PARCIAL/STATUS
        if is_status:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            try:
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    turno_ativo = await conn.fetchrow(
                        """
                        SELECT t.id, t.km_inicial, t.data_inicio, v.locadora, v.custo_aluguel_semanal, v.franquia_km_semanal, v.valor_km_excedente, v.escala_trabalho, m.meta_mensal_faturamento, m.dias_uteis_mes 
                        FROM public.turnos t 
                        JOIN public.veiculos v ON v.id = t.veiculo_id 
                        JOIN public.motoristas m ON m.id = t.motorista_id 
                        WHERE t.motorista_id = $1::uuid AND t.status IN ('ABERTO', 'em_andamento', 'em_pausa') 
                        ORDER BY t.data_inicio DESC LIMIT 1;
                        """, motorista_id
                    )
                    if turno_ativo:
                        turno_id = str(turno_ativo["id"])
                        dt_inicio = turno_ativo["data_inicio"]
                        tx = await conn.fetchrow(
                            """
                            SELECT COALESCE(SUM(valor), 0.00) as abastecido FROM public.transacoes 
                            WHERE motorista_id = $1::uuid AND turno_id = $2::uuid AND categoria = 'combustivel' AND estornado = FALSE;
                            """, motorista_id, turno_id
                        )
                        info_turno = {
                            "data_turno": dt_inicio.strftime('%d/%m/%Y'),
                            "data_inicio_hora": dt_inicio.strftime('%H:%M'),
                            "km_inicial": float(turno_ativo["km_inicial"]),
                            "locadora": turno_ativo["locadora"] or "Localiza Zarp",
                            "custo_aluguel_semanal": float(turno_ativo["custo_aluguel_semanal"] or 1020.85),
                            "franquia_km_semanal": float(turno_ativo["franquia_km_semanal"] or 1505.0),
                            "valor_km_excedente": float(turno_ativo["valor_km_excedente"] or 0.75),
                            "escala_trabalho": turno_ativo["escala_trabalho"] or "De quarta a segunda (6 dias)",
                            "meta_mensal": float(turno_ativo["meta_mensal_faturamento"] or 12000.0),
                            "dias_uteis": int(turno_ativo["dias_uteis_mes"] or 26),
                            "total_abastecido": float(tx["abastecido"])
                        }
                        resposta = formatar_relatorio_parcial(motorista["nome"], info_turno)
                    else:
                        resposta = "⚠ Você não possui uma jornada em andamento. Envie  *'Iniciar'*  com o seu odômetro para abrir o turno!"
            except Exception as e:
                logger.error(f"Erro ao obter status: {e}")
                resposta = "❌ Ocorreu um erro interno de banco de dados ao buscar seu status."
            await enviar_whatsapp(remote_jid, resposta)
            return

        # Extração de Odômetro (KM)
        numeros = re.findall(r'\d+', texto_limpo.replace('.', '').replace(',', ''))
        km_encontrado = next((float(num) for num in numeros if float(num) > 100), None)

        # =========================================================================
        # 3. INTERCEPÇÃO DA FSM DE JORNADA (Precedência Absoluta)
        # =========================================================================
        if estado_turno in ["AGUARDANDO_KM_INICIAL", "AGUARDANDO_KM_FINAL"] or (estado_turno and estado_turno.startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO")):
            km_digitado = converter_para_float(texto_bruto)

            # 3.1. Estado de Confirmação de Fechamento sem Lançamentos
            if estado_turno.startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO"):
                if any(term in texto_limpo for term in ["confirmar", "confirm", "sim", "confir", "ok", "isso", "certeza"]):
                    km_final = float(estado_turno.split("|km:")[1])
                    await enviar_whatsapp(remote_jid, "📊  *Confirmado faturamento zerado. Gerando DRE definitivo...*")
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_final)
                    await RedisFSMService.limpar_buffer(fsm_turno_key)
                    resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res) if res["sucesso"] else res['erro']
                    await enviar_whatsapp(remote_jid, resposta)
                else:
                    palavras_chave_financeiras = ['recebi', 'ganhei', 'faturei', 'corrida', 'uber', '99', 'gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado']
                    is_fin_event = any(w in texto_limpo for w in palavras_chave_financeiras)
                    if is_fin_event and km_digitado > 0:
                        is_desp = any(w in texto_limpo for w in ['gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado'])
                        cat_event = 'combustivel' if any(c in texto_limpo for c in ['gasolin', 'posto', 'combustiv', 'etanol', 'recarga', 'kwh', 'tomada']) else 'geral'
                        tipo_event = 'despesa' if is_desp else 'receita'
                        await TransacaoService.registrar_transacao(motorista_id, tipo_event, cat_event, km_digitado, texto_bruto, wpp_msg_id)
                        resposta = f"✅ Lançamento de  *R$ {km_digitado:.2f}*  guardado! Envie mais valores ou digite  *'Confirmar'*  para fechar o DRE:"
                    else:
                        resposta = "Entendido. Se quiser registrar transações, envie o valor acompanhado da descrição (ex:  *ganhei 150* ), ou responda  *'Confirmar'*  para fechar:"
                    await enviar_whatsapp(remote_jid, resposta)
                return

            # 3.2. Estados de Odômetro Pendente
            if km_digitado > 100:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    await enviar_whatsapp(remote_jid, "⚠ Nenhum veículo ativo localizado no seu cadastro.")
                    return

                if estado_turno == "AGUARDANDO_KM_INICIAL":
                    res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_digitado)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                        resposta = f"🚀 Turno aberto! Odômetro inicial lido como  **{km_digitado:,.1f} km** . Boa jornada, {motorista['nome']}!".replace(",", ".")
                    else:
                        tipo_erro = res.get("tipo_erro", "")
                        if tipo_erro == "TURNO_JA_ATIVO":
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                            await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                            resposta = res['erro']
                        else:
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                            await registrar_erro_e_verificar_escape(remote_jid, tenant_id, fsm_turno_key, res['erro'])
                            return
                    await enviar_whatsapp(remote_jid, resposta)

                elif estado_turno == "AGUARDANDO_KM_FINAL":
                    async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                        active_turno_row = await conn.fetchrow("SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;", motorista_id)
                        if active_turno_row:
                            total_tx = await TurnoService.verificar_transacoes_turno(motorista_id)
                            if total_tx == 0:
                                await RedisFSMService.definir_estado(fsm_turno_key, f"AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO|km:{km_digitado}")
                                resposta = (
                                    "⚠  *Atenção, motorista!*  Não encontrei nenhuma receita ou despesa registrada neste turno.\n\n"
                                    "Tem certeza absoluta que o faturamento de hoje foi R$ 0,00?\n\n"
                                    "Responda  *'Confirmar'* para fechar assim mesmo ou envie o valor de uma despesa/receita."
                                )
                                await enviar_whatsapp(remote_jid, resposta)
                                return
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
                            resposta = res['erro']
                        else:
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                            await registrar_erro_e_verificar_escape(remote_jid, tenant_id, fsm_turno_key, res['erro'])
                            return
                    await enviar_whatsapp(remote_jid, resposta)
                return
            else:
                await registrar_erro_e_verificar_escape(remote_jid, tenant_id, fsm_turno_key, "⚠ Por favor, envie apenas o número válido correspondente ao odômetro atual do painel (ex: 1399):")
                return

        # =========================================================================
        # 4. GESTÃO OPERACIONAL DE TURNO DIRETA
        # =========================================================================
        if tem_intencao_inicio:
            if km_encontrado:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    await enviar_whatsapp(remote_jid, "⚠ Nenhum veículo ativo localizado no seu cadastro.")
                    return
                await enviar_whatsapp(remote_jid, "⏳  *A validar coerência de quilometragem e abrindo turno...* ")
                res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_encontrado)
                if res["sucesso"]:
                    await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                    resposta = f"🚀 Turno aberto! Odômetro inicial registrado em  **{km_encontrado:,.1f} km** . Boa jornada, {motorista['nome']}!".replace(",", ".")
                else:
                    tipo_erro = res.get("tipo_erro", "")
                    if tipo_erro != "TURNO_JA_ATIVO":
                        await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                    resposta = res['erro']
                await enviar_whatsapp(remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                await enviar_whatsapp(remote_jid, "🟢 Beleza! Qual é a  **quilometragem atual**  do painel? (Ex: 1399)")
            return

        if tem_intencao_fim:
            if km_encontrado:
                # Verifica lançamentos antes do fechamento
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    active_turno_row = await conn.fetchrow("SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;", motorista_id)
                    if active_turno_row:
                        total_tx = await TurnoService.verificar_transacoes_turno(motorista_id)
                        if total_tx == 0:
                            await RedisFSMService.definir_estado(fsm_turno_key, f"AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO|km:{km_encontrado}")
                            resposta = (
                                "⚠  *Atenção, motorista!*  Não encontrei nenhuma receita ou despesa registrada neste turno.\n\n"
                                "Tem certeza absoluta que o faturamento de hoje foi R$ 0,00?\n\n"
                                "Responda  *'Confirmar'*  para fechar assim mesmo ou envie o valor de uma despesa/receita."
                            )
                            await enviar_whatsapp(remote_jid, resposta)
                            return
                await enviar_whatsapp(remote_jid, "📊  *A auditar movimentações e gerando DRE...* ")
                res = await TurnoService.fechar_turno_com_dre(motorista_id, km_encontrado)
                resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res) if res["sucesso"] else res['erro']
                await enviar_whatsapp(remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                await enviar_whatsapp(remote_jid, "🏁 Para gerar o seu DRE diário, qual é a  **quilometragem final**  no painel?")
            return

        if tem_intencao_pausa:
            res = await TurnoService.pausar_turno(motorista_id)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                resposta = "⏸ Turno pausado com sucesso. Quando voltar, envie  *'retomar'* !"
            else:
                resposta = f"⚠ {res['erro']}"
            await enviar_whatsapp(remote_jid, resposta)
            return

        if tem_intencao_retomada:
            res = await TurnoService.retomar_turno(motorista_id)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                resposta = "▶ Turno retomado com sucesso! Bom trabalho."
            else:
                resposta = f"⚠ {res['erro']}"
            await enviar_whatsapp(remote_jid, resposta)
            return

        # =========================================================================
        # 5. LANÇAMENTOS FINANCEIROS LIVRES (Fricção Zero com Trava de Palavra-Chave)
        # =========================================================================
        palavras_chave_financeiras = [
            'recebi', 'ganhei', 'faturei', 'corrida', 'uber', '99', 'indrive', 'faturamento', 'ganho',
            'gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 'etanol', 'gnv', 'recarga', 'kwh',
            'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado', 'oleo', 'pneu', 'abastec', 'reabastec', 'alcool'
        ]
        is_financeiro = any(w in texto_limpo for w in palavras_chave_financeiras)
        valor_transacao = converter_para_float(texto_bruto)

        if is_financeiro and valor_transacao > 0:
            is_despesa = any(
                w in texto_limpo for w in [
                    'gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 'etanol', 'gnv', 'reabastec', 'recarga',
                    'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado', 'oleo', 'pneu'
                ]
            )
            tipo = 'despesa' if is_despesa else 'receita'
            
            # Categoria
            if any(c in texto_limpo for c in ['gasolin', 'etanol', 'gnv', 'reabastec', 'recarga', 'posto', 'solar', 'casa', 'kwh', 'tomada', 'combustiv']):
                cat = 'combustivel'
            elif any(c in texto_limpo for c in ['almoco', 'marmita', 'lanche', 'refeicao', 'comida', 'rango']):
                cat = 'alimentacao'
            elif any(c in texto_limpo for c in ['lava', 'oleo', 'pneu', 'oficina', 'mecanico', 'manutencao']):
                cat = 'manutencao'
            else:
                cat = 'corrida' if tipo == 'receita' else 'geral'

            res_tx = await TransacaoService.registrar_transacao(
                motorista_id=motorista_id, tipo_movimentacao=tipo, categoria=cat, valor=valor_transacao, descricao=texto_bruto, wpp_msg_id=wpp_msg_id
            )
            if res_tx.get("status") == "success":
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                resposta = f"✅ Lançamento de  *R$ {valor_transacao:,.2f}*  guardado com sucesso no cofre! 🛡".replace(",", "X").replace(".", ",").replace("X", ".")
            else:
                if res_tx.get("status") == "duplicate":
                    resposta = "⚠ Este lançamento já foi guardado anteriormente no cofre contábil."
                else:
                    resposta = f"❌ Falha ao salvar no cofre contábil:\n_{res_tx.get('message')}_"
            await enviar_whatsapp(remote_jid, resposta)
            return

        # =========================================================================
        # 6. CATCH-ALL (Ajuda Contextual)
        # =========================================================================
        resposta_ajuda = (
            "🤖 Não reconheci a ação! Aqui tens os comandos rápidos:\n\n"
            "🟢  *Iniciar*  (ou 'iniciar 1399')\n"
            "🏁  *Fechar*  (ou 'fechar 1450')\n"
            "⏸  *Pausar*  /  *Retomar* \n"
            "📊  *Status*  (resumo do dia)\n"
            "💰  *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')\n\n"
            "Como posso ajudar agora?"
        )
        await enviar_whatsapp(remote_jid, resposta_ajuda)
