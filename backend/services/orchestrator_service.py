import re
import logging
import json
import unicodedata
import asyncio
from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal, InvalidOperation

# Importação dos serviços locais (Ground Truth)
from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.transacao_service import TransacaoService
from services.turno_service import TurnoService
from services.help_service import HelpService

# Configuração de Logs de Observabilidade
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s')
logger = logging.getLogger("OrchestratorService")

# =============================================================================
# CONFIGURAÇÕES E CONSTANTES DE REGEX
# =============================================================================

# Captura números seguidos de "km" ou números isolados acima de 100 (Odômetro)
REGEX_KM = re.compile(r'(\d+[.,]?\d*)\s*(?:km|quilometros|kms)?', re.IGNORECASE)

# Captura padrões monetários brasileiros (R$, $, reais)
REGEX_VALOR = re.compile(r'(?:R\$|\$)?\s*(\d+(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?|\d+)', re.IGNORECASE)

# Palavras-chave contextuais para categorias
REGEX_POSTO = re.compile(r'(?:posto|shell|ipiranga|petrobras|br|ale|combustivel|abastecimento|gasolina|etanol|gnv|recarga)', re.IGNORECASE)

# Gatilhos Financeiros de Alta Prioridade
FINANCIAL_TRIGGERS = ['ganhei', 'gastei', 'paguei', 'recebi', 'faturei', 'corrida', 'uber', '99', 'marmita', 'almoco']

# Mapeamento de intenções operacionais para Fuzzy Matching
INTENT_MAP = {
    "iniciar": ["iniciar", "comecar", "abrir", "bora", "turno", "inicio"],
    "fechar": ["fechar", "encerrar", "finalizar", "terminar", "fim"],
    "pausar": ["pausar", "pausa", "pause", "almocar", "intervalo"],
    "retomar": ["retomar", "voltar", "voltei", "continuar"],
    "status": ["status", "resumo", "parcial", "relatorio", "como estou"],
    "ajuda": ["ajuda", "help", "socorro", "como funciona"]
}

class OrchestratorService:
    """
    Serviço central de orquestração. Implementa a lógica de decisão do chatbot
    utilizando reconhecimento de padrões, similaridade de strings e FSM.
    """

    @staticmethod
    def normalizar_mensagem(texto: str) -> str:
        """Sanitiza strings removendo acentos e convertendo para lowercase."""
        if not texto:
            return ""
        nfkd = unicodedata.normalize('NFKD', texto)
        sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
        return re.sub(r'[^a-zA-Z0-9\s]', '', sem_acento).lower().strip()

    @staticmethod
    def converter_para_float(texto_valor: str) -> float:
        """Parser monetário resiliente a padrões brasileiros (Source Context)."""
        try:
            limpo = texto_valor.upper().replace("R$", "").replace("REAIS", "").strip()
            limpo = re.sub(r'[^\d.,]', '', limpo)
            if "," in limpo and "." in limpo:
                if limpo.find(".") < limpo.find(","):
                    limpo = limpo.replace(".", "").replace(",", ".")
                else:
                    limpo = limpo.replace(",", "")
            elif "," in limpo:
                limpo = limpo.replace(",", ".")
            return float(limpo)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def calcular_similaridade(s1: str, s2: str) -> float:
        """Implementação pura de Levenshtein para evitar dependências externas."""
        if len(s1) < len(s2):
            return OrchestratorService.calcular_similaridade(s2, s1)
        if len(s2) == 0:
            return 0.0
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        distancia = previous_row[-1]
        max_len = max(len(s1), len(s2))
        return 1.0 - (distancia / max_len)

    @staticmethod
    def extrair_entidades(texto: str) -> Dict[str, Any]:
        """Extrai KM, Valor Monetário e Categoria usando heurísticas de Regex."""
        entidades = {"km": None, "valor": 0.0, "categoria": "geral"}
        
        # 1. Extração de Odômetro (Prioridade > 100)
        kms = REGEX_KM.findall(texto)
        for val in kms:
            try:
                num = OrchestratorService.converter_para_float(val)
                if num > 100:
                    entidades["km"] = num
                    break
            except Exception:
                continue

        # 2. Extração Monetária
        valores = REGEX_VALOR.findall(texto)
        if valores:
            for v in valores:
                num_v = OrchestratorService.converter_para_float(v)
                # Evita que o odômetro seja capturado como valor financeiro
                if num_v != entidades["km"] and num_v > 0:
                    entidades["valor"] = num_v
                    break

        # 3. Categorização Contextual
        texto_low = texto.lower()
        if REGEX_POSTO.search(texto_low):
            entidades["categoria"] = "combustivel"
        elif any(k in texto_low for k in ['almoco', 'marmita', 'comer', 'lanche', 'rango']):
            entidades["categoria"] = "alimentacao"
        elif any(k in texto_low for k in ['lava', 'oficina', 'mecanico', 'pneu', 'oleo']):
            entidades["categoria"] = "manutencao"
        
        return entidades

    @staticmethod
    def identificar_intencao(texto_limpo: str) -> Optional[str]:
        """Detecta intenção com prioridade para comandos financeiros e fallback Fuzzy."""
        if not texto_limpo:
            return None

        # Prioridade 1: Gatilhos Financeiros Explícitos
        if any(w in texto_limpo for w in FINANCIAL_TRIGGERS):
            return "financeiro"

        # Prioridade 2: Match Exato Operacional
        palavras = texto_limpo.split()
        for intencao, sinonimos in INTENT_MAP.items():
            if any(s in palavras for s in sinonimos):
                return intencao

        # Prioridade 3: Fallback Fuzzy (Threshold dinâmico para strings curtas)
        melhor_intencao = None
        maior_score = 0.0
        threshold = 0.85 if len(texto_limpo) > 4 else 0.70
        
        for intencao, sinonimos in INTENT_MAP.items():
            for s in sinonimos:
                score = OrchestratorService.calcular_similaridade(texto_limpo, s)
                if score > maior_score:
                    maior_score = score
                    melhor_intencao = intencao
        
        if maior_score >= threshold:
            return melhor_intencao
            
        return None

    @staticmethod
    async def router(tenant_id: str, remote_jid: str, texto_bruto: str, wpp_msg_id: str) -> str:
        """
        Método Principal: Roteia a mensagem baseando-se no estado do motorista e intenção.
        """
        try:
            # Limpeza de Tenant ID (Apenas dígitos, últimos 11 caracteres)
            tel_clean = ''.join(filter(str.isdigit, tenant_id))[-11:]
            
            texto_limpo = OrchestratorService.normalizar_mensagem(texto_bruto)
            entidades = OrchestratorService.extrair_entidades(texto_bruto)
            
            # 1. Identificação do Motorista
            motorista = await DatabaseService.buscar_motorista_por_telefone(tel_clean)

            # =========================================================================
            # GESTÃO DE ONBOARDING (Motorista Novo)
            # =========================================================================
            if not motorista:
                fsm_key = f"onboard:{tel_clean}"
                estado = await RedisFSMService.obter_estado(fsm_key)
                
                if estado == "IDLE":
                    await RedisFSMService.definir_estado(fsm_key, "AG_NOME")
                    return "Bem-vindo ao *CFO Inteligente*! 🚀\nPercebi que você ainda não tem cadastro. Vamos começar?\n\nQual o seu *nome completo*?"
                
                elif estado == "AG_NOME":
                    if len(texto_bruto) < 3:
                        return "⚠ Nome muito curto. Digite seu nome completo:"
                    await RedisFSMService.definir_estado(fsm_key, f"AG_VEICULO|n:{texto_bruto}")
                    return f"Prazer, *{texto_bruto}*! 🚗\nQual o *modelo e marca* do seu veículo?"

                elif estado.startswith("AG_VEICULO"):
                    nome = estado.split("|n:")[1]
                    await RedisFSMService.definir_estado(fsm_key, f"AG_COMB|n:{nome}|v:{texto_bruto}")
                    return "Show! Qual o combustível? ⛽\n(Gasolina, Etanol, Flex, GNV, Hibrido ou Eletrico)"

                elif estado.startswith("AG_COMB"):
                    partes = estado.split("|")
                    comb_val = texto_limpo
                    if comb_val not in ["gasolina", "etanol", "flex", "gnv", "hibrido", "eletrico"]:
                        return "⚠ Escolha: Gasolina, Etanol, Flex, GNV, Hibrido ou Eletrico."
                    await RedisFSMService.definir_estado(fsm_key, f"{partes[1]}|{partes[2]}|c:{comb_val}|AG_PLACA")
                    return "Qual a *placa* do veículo? (Ex: ABC1D23)"

                elif "AG_PLACA" in estado:
                    placa = re.sub(r'[^A-Z0-9]', '', texto_bruto.upper())
                    if len(placa) != 7: return "⚠ Placa inválida (use 7 caracteres):"
                    await RedisFSMService.definir_estado(fsm_key, f"{estado}|p:{placa}|AG_TANQUE")
                    return "Qual a *capacidade do tanque* em litros? (Se elétrico, digite 0):"

                elif "AG_TANQUE" in estado:
                    tanque = OrchestratorService.converter_para_float(texto_bruto)
                    await RedisFSMService.definir_estado(fsm_key, f"{estado}|t:{tanque}|AG_BATERIA")
                    return "Qual a *capacidade da bateria* em kWh? (Se combustão pura, digite 0):"

                elif "AG_BATERIA" in estado:
                    bateria = OrchestratorService.converter_para_float(texto_bruto)
                    # Parsing final de metadados
                    metadata = dict(item.split(":") for item in estado.split("|") if ":" in item)
                    # Registro Atômico
                    motorista_uuid = await DatabaseService.registrar_novo_motorista(
                        telefone=tel_clean, nome=metadata['n'], veiculo_modelo=metadata['v'],
                        combustivel=metadata['c'], placa=metadata['p']
                    )
                    # Injeção de Capacidades Físicas (RLS Guarded)
                    async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                        estoque_json = {
                            "meta": {
                                "tipo_veiculo": metadata['c'], "capacidade_tanque_l": tanque,
                                "capacidade_bateria_kwh": bateria, "is_flex": metadata['c'] == "flex",
                                "is_hibrido": metadata['c'] == "hibrido", "is_eletrico": metadata['c'] == "eletrico"
                            }
                        }
                        await conn.execute("UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE motorista_id = $2::uuid", 
                                           json.dumps(estoque_json), motorista_uuid)
                    
                    await RedisFSMService.limpar_buffer(fsm_key)
                    return f"Cadastro concluído, *{metadata['n']}*! 🛡\nEnvie *'Iniciar'* com seu KM para começar!"

            # =========================================================================
            # PROCESSAMENTO DE COMANDOS (Motorista Ativo)
            # =========================================================================
            motorista_id = str(motorista["id"])
            fsm_key = f"turno_flow:{tel_clean}"
            intencao = OrchestratorService.identificar_intencao(texto_limpo)
            
            # Verificação de Escape por Erros
            erros = await RedisFSMService.obter_erros_consecutivos(tel_clean)
            if erros >= 3:
                await RedisFSMService.limpar_buffer(fsm_key)
                await RedisFSMService.limpar_erros_consecutivos(tel_clean)
                return "⚠ *Múltiplos erros!* Fluxo resetado. Tente: *Iniciar*, *Fechar* ou *Status*."

            estado_turno = await RedisFSMService.obter_estado(fsm_key)

            # 1. Tratamento de Confirmação de Turno Vazio (Zero Transação)
            if estado_turno.startswith("CONFIRM_ZERO"):
                if any(w in texto_limpo for w in ["sim", "confirmar", "ok", "isso"]):
                    km_final = float(estado_turno.split("|km:")[1])
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_final)
                    await RedisFSMService.limpar_buffer(fsm_key)
                    return "🏁 *Turno encerrado com faturamento R$ 0,00.* DRE gerado!"
                else:
                    await RedisFSMService.limpar_buffer(fsm_key) # Volta ao fluxo normal

            # 2. Fluxo de Início/Fim de Turno
            if intencao == "iniciar" or estado_turno == "AG_KM_INI":
                km = entidades["km"] or OrchestratorService.converter_para_float(texto_bruto)
                if km > 100:
                    veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                    res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_key)
                        return f"🚀 Turno aberto! KM: *{km:,.1f}*. Boa jornada!"
                    return f"⚠ {res['erro']}"
                await RedisFSMService.definir_estado(fsm_key, "AG_KM_INI")
                return "🟢 Qual o *KM atual* do painel?"

            elif intencao == "fechar" or estado_turno == "AG_KM_FIM":
                km = entidades["km"] or OrchestratorService.converter_para_float(texto_bruto)
                if km > 100:
                    # Auditoria de Lançamentos (Ground Truth rule)
                    tx_count = await TurnoService.verificar_transacoes_turno(motorista_id)
                    if tx_count == 0:
                        await RedisFSMService.definir_estado(fsm_key, f"CONFIRM_ZERO|km:{km}")
                        return "⚠ *Atenção:* Não vi ganhos hoje. Confirma faturamento *R$ 0,00*? (Responda 'Sim' ou envie o valor)"
                    
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_key)
                        return f"🏁 Turno encerrado em *{km:,.1f} km*. DRE gerado com sucesso!"
                    return f"⚠ {res['erro']}"
                await RedisFSMService.definir_estado(fsm_key, "AG_KM_FIM")
                return "🏁 Qual o *KM final* para fechar o dia?"

            # 3. Fluxo Financeiro (Lançamentos Livres)
            elif intencao == "financeiro" or entidades["valor"] > 0:
                tipo = "despesa" if any(w in texto_limpo for w in ['gastei', 'paguei', 'posto', 'marmita']) else "receita"
                res_tx = await TransacaoService.registrar_transacao(
                    motorista_id=motorista_id, tipo_movimentacao=tipo,
                    categoria=entidades["categoria"], valor=entidades["valor"],
                    descricao=texto_bruto, wpp_msg_id=wpp_msg_id
                )
                if res_tx["status"] == "success":
                    await RedisFSMService.limpar_erros_consecutivos(tel_clean)
                    return f"✅ Lançamento de *R$ {entidades['valor']:.2f}* ({entidades['categoria']}) guardado! 🛡"
                return f"⚠ {res_tx.get('message', 'Erro ao salvar')}"

            # 4. Outros Comandos
            elif intencao == "status":
                return "📊 *Resumo Parcial:* (Consultando banco...)\nUse 'Status' para ver sua meta diária."
            elif intencao == "ajuda":
                return HelpService.obter_ajuda("geral")

            # Catch-all
            await RedisFSMService.incrementar_erros_consecutivos(tel_clean)
            logger.warning(f"Mensagem não reconhecida de {tel_clean}: {texto_bruto}")
            return "🤖 Não entendi. Tente:\n- *Iniciar 12500*\n- *Ganhei 50 uber*\n- *Gastei 100 posto*\n- *Fechar 12600*"

        except Exception as e:
            logger.error(f"Erro crítico no Orchestrator [MsgID: {wpp_msg_id}]: {e}", exc_info=True)
            return "❌ Tive um problema técnico. Tente novamente em instantes."
