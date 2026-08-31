import os
import re
import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Any, Optional
import asyncpg
from services.database_service import DatabaseService

# Configuração do Logger de Observabilidade para Rastreabilidade SRE
logger = logging.getLogger(__name__)

class TransacaoService:
    """
    Serviço Financeiro (Ledger) de Alta Precisão (Bank-Grade).
    
    Implementa a lógica de telemetria e recalibração dinâmica para veículos Híbridos, 
    Flex e Elétricos. Gerencia estoques químicos e elétricos via JSONB, garantindo 
    idempotência contábil e integridade aritmética com arredondamento ROUND_HALF_UP.
    """

    # Constantes de Validação e Fallbacks Financeiros
    _TIPOS_MOVIMENTACAO_VALIDOS = {"receita", "despesa"}
    _PRECO_MEDIO_LITRO_FALLBACK = Decimal("5.85")
    _PRECO_MEDIO_KWH_FALLBACK = Decimal("1.20")

    @staticmethod
    def _normalizar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        """Sanitiza strings para normalização de domínio."""
        return tipo_movimentacao.strip().lower()

    @staticmethod
    def _validar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        """Garante a integridade do domínio de movimentação."""
        tipo_normalizado = TransacaoService._normalizar_tipo_movimentacao(tipo_movimentacao)
        if tipo_normalizado not in TransacaoService._TIPOS_MOVIMENTACAO_VALIDOS:
            raise ValueError("O tipo de movimentação deve ser 'receita' ou 'despesa'.")
        return tipo_normalizado

    @staticmethod
    def _validar_valor(valor: float, permitir_zero: bool = False) -> Decimal:
        """
        Converte e valida valores financeiros para precisão Decimal.
        Utiliza strings como intermediário para evitar imprecisões de ponto flutuante IEEE 754.
        """
        try:
            # Arredondamento para 2 casas decimais (Bank-Grade)
            valor_decimal = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Valor financeiro mal formatado.") from exc

        if permitir_zero:
            if valor_decimal < 0:
                raise ValueError("O valor financeiro não pode ser negativo.")
        else:
            if valor_decimal <= 0:
                raise ValueError("O valor financeiro deve ser estritamente maior que zero.")
        
        return valor_decimal

    @staticmethod
    def _mapear_erro_postgres(exc: Exception) -> Dict[str, Any]:
        """Tradução de exceções relacionais para mensagens de negócio (Domain Driven Errors)."""
        mensagem = str(exc)
        if "PERIODO_FECHADO" in mensagem:
            return {
                "status": "error",
                "message": "⚠ O período contábil já foi consolidado e trancado pela contabilidade. Não são permitidas alterações retroativas.",
                "error_code": "PERIODO_FECHADO"
            }
        return {
            "status": "error",
            "message": f"Falha na integridade do cofre contábil: {mensagem}",
            "error_code": "ERRO_BANCO"
        }

    @staticmethod
    async def registrar_transacao(
        motorista_id: str,
        tipo_movimentacao: str,
        categoria: str,
        valor: float,
        descricao: Optional[str] = None,
        wpp_msg_id: Optional[str] = None,
        # Telemetria de abastecimento guiado (opcionais — populados pelo fluxo FSM)
        litros_abastecidos: Optional[float] = None,
        preco_por_litro: Optional[float] = None,
        odometro_abastecimento: Optional[float] = None,
        tanque_cheio: bool = False,
        # Tipo de combustível explícito — elimina dependência do fallback 50/50 para veículos Flex.
        # Valores aceitos: 'gasolina' | 'etanol' | 'gnv' | 'eletrico' | None (detecta pela descrição)
        tipo_combustivel_abastecido: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Registra movimentações financeiras com interceptação de telemetria.
        Realiza recalibração atômica de tanques/baterias em eventos de abastecimento.
        Quando tipo_combustivel_abastecido é fornecido, a detecção do mix Flex usa o valor
        explícito com precedência sobre a análise textual da descrição.
        """
        desc_limpa = (descricao or "").lower()
        
        # Detecção de gratuidade baseada no Ground Truth (Solar, Casa, Grátis, Tomada)
        is_recarga_gratuita = any(term in desc_limpa for term in ["solar", "casa", "gratis", "tomada"])
        permitir_zero = categoria.lower() == "combustivel" and is_recarga_gratuita

        try:
            tipo_validado = TransacaoService._validar_tipo_movimentacao(tipo_movimentacao)
            valor_decimal = TransacaoService._validar_valor(valor, permitir_zero=permitir_zero)
        except ValueError as exc:
            return {"status": "error", "message": f"❌ {str(exc)}", "error_code": "VALIDACAO"}

        try:
            # get_tenant_connection injeta RLS e abre transação atômica implicitamente
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Identificação de Turno Ativo
                turno_ativo = await conn.fetchrow(
                    """
                    SELECT id, veiculo_id FROM public.turnos 
                    WHERE motorista_id = $1::uuid AND status IN ('em_andamento', 'em_pausa', 'ABERTO', 'PAUSADO')
                    ORDER BY data_inicio DESC LIMIT 1;
                    """, motorista_id
                )
                
                turno_id = str(turno_ativo["id"]) if turno_ativo else None
                veiculo_id = str(turno_ativo["veiculo_id"]) if turno_ativo else None

                if not veiculo_id:
                    veiculo_row = await conn.fetchrow(
                        "SELECT id FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE LIMIT 1;", 
                        motorista_id
                    )
                    veiculo_id = str(veiculo_row["id"]) if veiculo_row else None

                # 2. Telemetria e Recalibração de Combustível
                if tipo_validado == "despesa" and categoria.lower() == "combustivel" and veiculo_id:
                    # FOR UPDATE serializa escritas concorrentes no mesmo veículo.
                    # Sem isso, dois abastecimentos chegando em paralelo no pool
                    # leriam o mesmo snapshot de estoque e um sobrescreveria o outro.
                    veiculo = await conn.fetchrow(
                        """SELECT estoque_financeiro, tipo_combustivel,
                                  is_hibrido, is_eletrico, is_flex, capacidade_bateria
                           FROM public.veiculos WHERE id = $1::uuid FOR UPDATE;""",
                        veiculo_id
                    )
                    
                    if veiculo:
                        estoque_raw = veiculo["estoque_financeiro"]
                        estoque = json.loads(estoque_raw) if isinstance(estoque_raw, str) else (estoque_raw or {})
                        
                        # Bloco de Retrocompatibilidade (Backward Compatibility) - GROUND TRUTH
                        if "meta" not in estoque:
                            tipo_comb_raw = (veiculo["tipo_combustivel"] or "").lower()
                            _is_eletrico_raw = "eletrico" in tipo_comb_raw or "hibrido" in tipo_comb_raw
                            estoque["meta"] = {
                                "tipo_veiculo": tipo_comb_raw or "gasolina",
                                "is_flex": "flex" in tipo_comb_raw,
                                "is_hibrido": "hibrido" in tipo_comb_raw,
                                "is_eletrico": "eletrico" in tipo_comb_raw,
                                "capacidade_tanque_l": 50.0,
                                # 0.0 para veículos não-elétricos; evita aceitar recargas elétricas
                                # em veículos a combustão. Elétricos/híbridos devem ter o campo
                                # capacidade_bateria populado via cadastro.
                                "capacidade_bateria_kwh": 0.0,
                                "qtd_tanques": 1
                            }
                        if "liquido" not in estoque:
                            estoque["liquido"] = {
                                "litros": 0.0, "custo_total": 0.0, "gasolina_litros": 0.0, "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0, "km_l_gasolina": 12.0, "km_l_etanol": 8.5
                            }
                        if "eletricidade" not in estoque:
                            estoque["eletricidade"] = {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5}
                        
                        meta = estoque["meta"]
                        capacidade_tanque = Decimal(str(meta.get("capacidade_tanque_l", "50.0")))
                        capacidade_bateria = Decimal(str(meta.get("capacidade_bateria_kwh", "0.0")))
                        tipo_veiculo = (veiculo["tipo_combustivel"] or meta.get("tipo_veiculo", "gasolina")).lower()

                        # Detecção de Energia (Regex Expandida conforme Ground Truth)
                        is_eletrico_event = (
                            "kwh" in desc_limpa or "recarga" in desc_limpa or "solar" in desc_limpa or
                            "eletrico" in desc_limpa or bool(meta.get("is_eletrico", False))
                        )
                        is_gnv_event = (
                            "gnv" in desc_limpa or "gas natural" in desc_limpa or
                            "m3" in desc_limpa or tipo_veiculo == "gnv"
                        )

                        # 2.1. Recarga Elétrica
                        if is_eletrico_event:
                            dados_elet = estoque["eletricidade"]
                            qtd_atual = Decimal(str(dados_elet.get("kwh", 0.0)))
                            
                            kwh_match = re.search(r'(\d+[.,]?\d*)\s*(?:kwh|kw|quilowatt)', desc_limpa)
                            if kwh_match:
                                kwh_novos = Decimal(kwh_match.group(1).replace(',', '.'))
                            else:
                                if is_recarga_gratuita:
                                    kwh_novos = Decimal("15.00") # Fallback Ground Truth para carga solar/doméstica
                                else:
                                    kwh_novos = (valor_decimal / TransacaoService._PRECO_MEDIO_KWH_FALLBACK)

                            novo_volume = (qtd_atual + kwh_novos).quantize(Decimal("0.01"), ROUND_HALF_UP)
                            if novo_volume > capacidade_bateria:
                                vazio_disponivel = capacidade_bateria - qtd_atual
                                return {
                                    "status": "error",
                                    "message": (
                                        f"⚠ *Capacidade da Bateria Excedida!* \nUma recarga de *{kwh_novos:.2f} kWh* "
                                        f"excede a capacidade (*{capacidade_bateria:.1f} kWh*).\n\n"
                                        f"Sua bateria já possui *{qtd_atual:.1f} kWh*. Você pode adicionar no máximo *{vazio_disponivel:.2f} kWh*."
                                    ),
                                    "error_code": "EXCEDE_CAPACIDADE"
                                }
                            
                            dados_elet["kwh"] = float(novo_volume)
                            dados_elet["custo_total"] = float((Decimal(str(dados_elet["custo_total"])) + valor_decimal).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            estoque["eletricidade"] = dados_elet

                        # 2.2. Abastecimento GNV (m³)
                        elif is_gnv_event:
                            if "gnv" not in estoque:
                                estoque["gnv"] = {"m3": 0.0, "custo_total": 0.0, "km_m3": 14.0}
                            dados_gnv = estoque["gnv"]
                            m3_atual = Decimal(str(dados_gnv.get("m3", 0.0)))

                            m3_match = re.search(r'(\d+[.,]?\d*)\s*(?:m3|metros? cubicos?|m³)', desc_limpa)
                            if m3_match:
                                m3_novos = Decimal(m3_match.group(1).replace(',', '.'))
                            else:
                                # Preço médio de referência GNV: ~3,50/m³
                                m3_novos = (valor_decimal / Decimal("3.50")).quantize(Decimal("0.01"), ROUND_HALF_UP)

                            dados_gnv["m3"] = float((m3_atual + m3_novos).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_gnv["custo_total"] = float((Decimal(str(dados_gnv["custo_total"])) + valor_decimal).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            estoque["gnv"] = dados_gnv

                        # 2.3. Abastecimento Líquido (Mix Químico Flex/Gasolina/Etanol)
                        else:
                            dados_liq = estoque["liquido"]

                            # ── UNDERFLOW GUARD ──────────────────────────────────────────────────
                            # Estoque negativo acumula-se quando a queima declarada excede o cofre.
                            # Clampar para 0 antes de qualquer operação impede que o abastecimento
                            # subsequente receba um volume base negativo, o que causaria:
                            #   (a) capacidade aparente menor → falsa trava "EXCEDE_CAPACIDADE"
                            #   (b) custo_total / (litros negativos) → pico artificial de CMP
                            qtd_atual = max(Decimal("0.00"), Decimal(str(dados_liq.get("litros", 0.0))))
                            gas_atual = max(Decimal("0.00"), Decimal(str(dados_liq.get("gasolina_litros", 0.0))))
                            eta_atual = max(Decimal("0.00"), Decimal(str(dados_liq.get("etanol_litros", 0.0))))
                            custo_atual = max(Decimal("0.00"), Decimal(str(dados_liq.get("custo_total", 0.0))))
                            # Normaliza sub-litros para que somem ao total clamped
                            _sub_total = gas_atual + eta_atual
                            if _sub_total > qtd_atual and _sub_total > Decimal("0"):
                                # Sub-totais maiores que o total clamped: reescala proporcionalmente
                                _fator = qtd_atual / _sub_total
                                gas_atual = (gas_atual * _fator).quantize(Decimal("0.01"), ROUND_HALF_UP)
                                eta_atual = (eta_atual * _fator).quantize(Decimal("0.01"), ROUND_HALF_UP)
                            # ─────────────────────────────────────────────────────────────────────

                            # Hierarquia de fonte de verdade para o volume abastecido:
                            # 1. Parâmetro calculado pela FSM (preco informado pelo motorista)
                            # 2. Extração textual da descrição ("abasteci 17,5 litros")
                            # 3. Fallback estatístico pelo preço médio de referência
                            if litros_abastecidos is not None and litros_abastecidos > 0:
                                litros_novos = Decimal(str(litros_abastecidos))
                            else:
                                litros_match = re.search(r'(\d+[.,]?\d*)\s*(?:litros|litro|l)', desc_limpa)
                                if litros_match:
                                    litros_novos = Decimal(litros_match.group(1).replace(',', '.'))
                                else:
                                    litros_novos = (valor_decimal / TransacaoService._PRECO_MEDIO_LITRO_FALLBACK)

                            # ── TANQUE-CHEIO SELF-HEAL ───────────────────────────────────────────
                            # Quando o motorista confirma tanque cheio, sabemos com certeza física
                            # que o volume real = capacidade nominal.  Descartamos o saldo arrastado,
                            # ancorámos ao máximo e recalculámos o CMP pelo preço desta nota fiscal.
                            # Isso corrige desvios acumulados por digitação imprecisa de odômetros,
                            # abastecimentos sem litros declarados e underflows históricos.
                            if tanque_cheio and capacidade_tanque > Decimal("0"):
                                litros_base = capacidade_tanque
                                custo_base  = (capacidade_tanque * (valor_decimal / litros_novos)).quantize(
                                    Decimal("0.01"), ROUND_HALF_UP
                                ) if litros_novos > Decimal("0") else valor_decimal
                                # Redefine proporção química ao combustível puro abastecido
                                _tipo_expl_heal = (tipo_combustivel_abastecido or "").lower().strip()
                                _is_gas_heal = _tipo_expl_heal == "gasolina" or (
                                    not _tipo_expl_heal and ("gasolina" in desc_limpa or (
                                        "gas" in desc_limpa and "gnv" not in desc_limpa))
                                )
                                _is_eta_heal = _tipo_expl_heal == "etanol" or (
                                    not _tipo_expl_heal and ("etanol" in desc_limpa or "alcool" in desc_limpa)
                                )
                                if _is_gas_heal:
                                    dados_liq["gasolina_litros"]  = float(litros_base)
                                    dados_liq["etanol_litros"]    = 0.0
                                    dados_liq["gasolina_proporcao"] = 1.0
                                    dados_liq["etanol_proporcao"]   = 0.0
                                elif _is_eta_heal:
                                    dados_liq["gasolina_litros"]  = 0.0
                                    dados_liq["etanol_litros"]    = float(litros_base)
                                    dados_liq["gasolina_proporcao"] = 0.0
                                    dados_liq["etanol_proporcao"]   = 1.0
                                else:
                                    # Flex sem especificação: preserva proporção existente sobre novo total
                                    _p_gas = Decimal(str(dados_liq.get("gasolina_proporcao", 1.0)))
                                    _p_eta = Decimal(str(dados_liq.get("etanol_proporcao", 0.0)))
                                    dados_liq["gasolina_litros"] = float((litros_base * _p_gas).quantize(Decimal("0.01"), ROUND_HALF_UP))
                                    dados_liq["etanol_litros"]   = float((litros_base * _p_eta).quantize(Decimal("0.01"), ROUND_HALF_UP))
                                dados_liq["litros"]      = float(litros_base)
                                dados_liq["custo_total"] = float(custo_base)
                                estoque["liquido"] = dados_liq
                                logger.info(
                                    f"[TransacaoService] Tanque-cheio self-heal: motorista={motorista_id} "
                                    f"capacidade={litros_base} L | novo_cmp=R$ {float(valor_decimal/litros_novos):.4f}/L"
                                    if litros_novos > 0 else
                                    f"[TransacaoService] Tanque-cheio self-heal: motorista={motorista_id} capacidade={litros_base} L"
                                )
                                await conn.execute(
                                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                                    json.dumps(estoque), veiculo_id
                                )
                                # Persiste a transação no ledger e retorna — sem re-entrar no mix math
                                litros_dec = litros_novos.quantize(Decimal("0.01"), ROUND_HALF_UP)
                                preco_dec  = (valor_decimal / litros_novos).quantize(Decimal("0.0001"), ROUND_HALF_UP) if litros_novos > 0 else None
                                odo_dec    = Decimal(str(odometro_abastecimento)).quantize(Decimal("0.01"), ROUND_HALF_UP) if odometro_abastecimento is not None else None
                                row = await conn.fetchrow(
                                    """
                                    INSERT INTO public.transacoes (
                                        motorista_id, turno_id, veiculo_id, tipo_movimentacao, categoria,
                                        valor, descricao, wpp_msg_id,
                                        litros_abastecidos, preco_por_litro, odometro_abastecimento, tanque_cheio
                                    ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                                    ON CONFLICT (wpp_msg_id) DO NOTHING
                                    RETURNING id, data_transacao;
                                    """,
                                    motorista_id, turno_id, veiculo_id, tipo_validado, categoria,
                                    valor_decimal, descricao, wpp_msg_id,
                                    litros_dec, preco_dec, odo_dec, True,
                                )
                                if row is None:
                                    return {"status": "duplicate", "message": "⚠ Esse lançamento já foi guardado anteriormente no cofre contábil.", "error_code": "DUPLICADA"}

                                # ── RECALIBRAÇÃO FULL-TO-FULL ─────────────────────────────────
                                # Executada APÓS o INSERT para que o odômetro atual já esteja
                                # disponível como ponto de referência nas próximas verificações.
                                # A conexão está dentro da mesma transação atômica — se a
                                # recalibração falhar, o INSERT já está garantido pelo RETURNING.
                                sugestao_recalibracao = await TransacaoService._recalibrar_rendimento_full_to_full(
                                    conn=conn,
                                    veiculo_id=veiculo_id,
                                    odometro_atual=odometro_abastecimento,
                                    litros_abastecidos_atual=float(litros_novos) if litros_novos > 0 else None,
                                    tipo_combustivel=tipo_combustivel_abastecido,
                                    estoque=estoque,
                                )
                                # ─────────────────────────────────────────────────────────────
                                return {
                                    "status": "success",
                                    "message": f"✅ Tanque self-healed! Cofre ancorado a  *{float(litros_base):.1f} L*  (capacidade nominal). 🛡",
                                    "transacao_id": str(row["id"]),
                                    "data_transacao": row["data_transacao"],
                                    "self_healed": True,
                                    "sugestao_recalibracao": sugestao_recalibracao,
                                }
                            # ─────────────────────────────────────────────────────────────────────

                            novo_vol_liq = (qtd_atual + litros_novos).quantize(Decimal("0.01"), ROUND_HALF_UP)
                            if novo_vol_liq > capacidade_tanque:
                                vazio_disponivel = capacidade_tanque - qtd_atual
                                return {
                                    "status": "error",
                                    "message": (
                                        f"⚠ *Capacidade do Tanque Excedida!* \nO abastecimento de *{litros_novos:.2f} L* "
                                        f"excede a capacidade (*{capacidade_tanque:.1f} L*).\n\n"
                                        f"Seu tanque já possui *{qtd_atual:.1f} L*. Você pode adicionar no máximo *{vazio_disponivel:.2f} L*."
                                    ),
                                    "error_code": "EXCEDE_CAPACIDADE"
                                }

                            # Lógica de Mix Químico Refinada
                            # Prioridade 1: parâmetro explícito da FSM guiada
                            _tipo_expl = (tipo_combustivel_abastecido or "").lower().strip()
                            # Prioridade 2: detecção textual na descrição
                            is_gasolina = _tipo_expl == "gasolina" or (
                                not _tipo_expl and ("gasolina" in desc_limpa or ("gas" in desc_limpa and "gnv" not in desc_limpa))
                            )
                            is_etanol = _tipo_expl == "etanol" or (
                                not _tipo_expl and ("etanol" in desc_limpa or "alcool" in desc_limpa or "alc" in desc_limpa)
                            )

                            # Use the clamped sub-values from the Underflow Guard above
                            gas_litros = gas_atual
                            eta_litros = eta_atual

                            if is_gasolina:
                                gas_litros += litros_novos
                            elif is_etanol:
                                eta_litros += litros_novos
                            else:
                                # Fallback apenas quando o tipo é genuinamente desconhecido:
                                # usa o combustível principal do veículo (ou 50/50 para Flex sem especificação)
                                tipo_veiculo_norm = (veiculo["tipo_combustivel"] or "").lower()
                                if tipo_veiculo_norm == "etanol":
                                    eta_litros += litros_novos
                                elif bool(meta.get("is_flex", False)) or bool(meta.get("is_hibrido", False)):
                                    # 50/50 só chega aqui se o motorista não especificou o combustível
                                    # nem via parâmetro explícito nem via descrição textual
                                    gas_litros += litros_novos / 2
                                    eta_litros += litros_novos / 2
                                else:
                                    gas_litros += litros_novos

                            total_tanque = gas_litros + eta_litros
                            if total_tanque > 0:
                                # Precisão de 4 casas para proporção (Engenharia Química)
                                dados_liq["gasolina_proporcao"] = float((gas_litros / total_tanque).quantize(Decimal("0.0001"), ROUND_HALF_UP))
                                dados_liq["etanol_proporcao"] = float((eta_litros / total_tanque).quantize(Decimal("0.0001"), ROUND_HALF_UP))
                            
                            dados_liq["gasolina_litros"] = float(gas_litros.quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_liq["etanol_litros"] = float(eta_litros.quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_liq["litros"] = float(total_tanque.quantize(Decimal("0.01"), ROUND_HALF_UP))
                            # Usa custo_atual (clamped) como base — nunca soma sobre valor negativo
                            dados_liq["custo_total"] = float((custo_atual + valor_decimal).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            estoque["liquido"] = dados_liq

                        # Persistência Atômica da recalibração no JSONB
                        await conn.execute(
                            "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                            json.dumps(estoque), veiculo_id
                        )

                # 3. Inserção no Livro-Razão (Idempotência via wpp_msg_id)
                # Campos de telemetria são NULLABLE — populados apenas pelo fluxo guiado.
                litros_dec = Decimal(str(litros_abastecidos)).quantize(Decimal("0.01"), ROUND_HALF_UP) if litros_abastecidos is not None else None
                preco_dec  = Decimal(str(preco_por_litro)).quantize(Decimal("0.0001"), ROUND_HALF_UP) if preco_por_litro is not None else None
                odo_dec    = Decimal(str(odometro_abastecimento)).quantize(Decimal("0.01"), ROUND_HALF_UP) if odometro_abastecimento is not None else None

                query_insert = """
                INSERT INTO public.transacoes (
                    motorista_id, turno_id, veiculo_id, tipo_movimentacao, categoria,
                    valor, descricao, wpp_msg_id,
                    litros_abastecidos, preco_por_litro, odometro_abastecimento, tanque_cheio
                ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (wpp_msg_id) DO NOTHING
                RETURNING id, data_transacao;
                """
                row = await conn.fetchrow(
                    query_insert,
                    motorista_id, turno_id, veiculo_id, tipo_validado, categoria,
                    valor_decimal, descricao, wpp_msg_id,
                    litros_dec, preco_dec, odo_dec, tanque_cheio,
                )

                if row is None:
                    logger.warning(f"Lançamento duplicado ignorado: {wpp_msg_id}")
                    return {
                        "status": "duplicate", 
                        "message": "⚠ Esse lançamento já foi guardado anteriormente no cofre contábil.",
                        "error_code": "DUPLICADA"
                    }

                valor_fmt = f"R$ {float(valor_decimal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                return {
                    "status": "success",
                    "message": f"✅ Lançamento de *{valor_fmt}* guardado no cofre! 🛡",
                    "transacao_id": str(row["id"]),
                    "data_transacao": row["data_transacao"]
                }

        except asyncpg.PostgresError as exc:
            logger.exception("Erro crítico de banco de dados.")
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def _recalibrar_rendimento_full_to_full(
        conn,
        veiculo_id: str,
        odometro_atual: float,
        litros_abastecidos_atual: float,
        tipo_combustivel: Optional[str],
        estoque: dict,
    ) -> Optional[Dict[str, Any]]:
        """Recalibração automática de rendimento pelo método Full-to-Full.

        Quando dois abastecimentos com tanque_cheio=TRUE seguidos têm odômetro registrado,
        podemos calcular o rendimento real:

            km/L_real = (odometro_atual - odometro_anterior) / Σ litros_no_intervalo

        O resultado é comparado com o parâmetro configurado no JSONB. Se a divergência
        for > 5%, retorna um dict de sugestão; caso contrário retorna None (sem ação).

        Casos de aborto silencioso (sem sugestão):
        - Sem abastecimento anterior com tanque_cheio + odômetro
        - km rodados < 50 (muito curto para ser estatisticamente relevante)
        - km rodados > 2.000 (provavelmente odômetro errado — evita sugestões absurdas)
        - Litros no intervalo < 5 (dados insuficientes)
        - Divergência ≤ 5% (dentro da margem de ruído aceitável)

        Não lança exceção — falhas são logadas e retornam None (nunca bloqueiam o fluxo).
        """
        try:
            if odometro_atual is None or odometro_atual <= 0:
                return None

            # Busca o abastecimento tanque_cheio imediatamente anterior com odômetro registrado.
            # O índice idx_transacoes_recalibracao_telemetria cobre esta query.
            anterior = await conn.fetchrow(
                """
                SELECT odometro_abastecimento, data_transacao
                FROM public.transacoes
                WHERE veiculo_id = $1::uuid
                  AND tanque_cheio = TRUE
                  AND odometro_abastecimento IS NOT NULL
                  AND estornado = FALSE
                  AND categoria = 'combustivel'
                  AND odometro_abastecimento < $2
                ORDER BY odometro_abastecimento DESC
                LIMIT 1;
                """,
                veiculo_id, odometro_atual,
            )
            if not anterior:
                return None  # Primeiro tanque cheio registrado — sem base de comparação

            odo_anterior = Decimal(str(anterior["odometro_abastecimento"]))
            odo_atual = Decimal(str(odometro_atual))
            km_intervalo = odo_atual - odo_anterior

            # Filtra intervalos improváveis: muito curtos ou muito longos
            if km_intervalo < Decimal("50") or km_intervalo > Decimal("2000"):
                logger.info(
                    f"[Full-to-Full] Intervalo descartado: {float(km_intervalo):.0f} km "
                    f"(fora de [50, 2000]) — veiculo={veiculo_id}"
                )
                return None

            # Soma todos os litros abastecidos (tanque cheio ou não) entre os dois checkpoints
            litros_intervalo_row = await conn.fetchval(
                """
                SELECT COALESCE(SUM(litros_abastecidos), 0)
                FROM public.transacoes
                WHERE veiculo_id = $1::uuid
                  AND categoria = 'combustivel'
                  AND estornado = FALSE
                  AND litros_abastecidos IS NOT NULL
                  AND data_transacao > $2
                  AND odometro_abastecimento <= $3;
                """,
                veiculo_id,
                anterior["data_transacao"],
                odometro_atual,
            )
            litros_intervalo = Decimal(str(litros_intervalo_row or "0"))

            # Inclui os litros do abastecimento atual se não foram contados acima
            if litros_abastecidos_atual and litros_abastecidos_atual > 0:
                litros_intervalo += Decimal(str(litros_abastecidos_atual))

            if litros_intervalo < Decimal("5"):
                return None  # Dados insuficientes para cálculo confiável

            km_l_real = (km_intervalo / litros_intervalo).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Lê o sub-dict do estoque líquido — necessário tanto para o sanity check
            # quanto para resolver o parâmetro configurado de rendimento.
            liq = estoque.get("liquido", {})

            # ── SANITY CHECK FÍSICO (DINÂMICO) ───────────────────────────────────
            # O limite INFERIOR é fixo: 4.5 km/L é o piso absoluto para qualquer
            # veículo a combustão no Brasil (SUV grande em cidade congestionada).
            #
            # O limite SUPERIOR é DINÂMICO: calculado como 1.5× o melhor rendimento
            # cadastrado pelo próprio motorista. Isso torna o check correto tanto
            # para carros (km_l_gas ≈ 12 → teto ≈ 18 km/L) quanto para motos
            # (km_l_gas ≈ 35 → teto ≈ 52 km/L), sem precisar de um campo
            # 'categoria_veiculo' no banco — a própria calibração do motorista
            # é a fonte de verdade sobre o rendimento esperado do seu veículo.
            #
            # O `max(..., Decimal("12.0"))` garante que veículos com parâmetros
            # ainda no valor padrão (não calibrados) não rejeitem motos logo no
            # primeiro ciclo Full-to-Full (teto mínimo efetivo = 18 km/L).
            _KM_L_MIN     = Decimal("4.5")
            _km_l_gas_cfg = Decimal(str(liq.get("km_l_gasolina", "12.0")))
            _km_l_eta_cfg = Decimal(str(liq.get("km_l_etanol",   "8.5")))
            _km_l_melhor  = max(_km_l_gas_cfg, _km_l_eta_cfg, Decimal("12.0"))
            _KM_L_MAX     = (_km_l_melhor * Decimal("1.5")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if km_l_real < _KM_L_MIN or km_l_real > _KM_L_MAX:
                logger.warning(
                    f"[Full-to-Full] Sanity check reprovado: km/L_real={float(km_l_real):.2f} "
                    f"fora de [{_KM_L_MIN}, {_KM_L_MAX}] — possível abastecimento parcial omitido. "
                    f"veiculo={veiculo_id} | intervalo={float(km_intervalo):.0f} km | "
                    f"litros={float(litros_intervalo):.2f} L"
                )
                return None
            # ─────────────────────────────────────────────────────────────────────

            # Resolve o parâmetro de rendimento configurado para o combustível abastecido
            tipo_norm = (tipo_combustivel or "").lower().strip()
            if tipo_norm == "etanol":
                km_l_configurado = Decimal(str(liq.get("km_l_etanol", "8.5")))
                param_nome = "km etanol"
                param_label = "Etanol"
            else:
                # Gasolina é o fallback padrão (inclui GNV não-especificado e veículos mono-fuel)
                km_l_configurado = Decimal(str(liq.get("km_l_gasolina", "12.0")))
                param_nome = "km gasolina"
                param_label = "Gasolina"

            if km_l_configurado <= Decimal("0"):
                return None

            divergencia = abs(km_l_real - km_l_configurado) / km_l_configurado

            logger.info(
                f"[Full-to-Full] veiculo={veiculo_id} | intervalo={float(km_intervalo):.0f} km | "
                f"litros={float(litros_intervalo):.2f} L | km/L_real={float(km_l_real):.2f} | "
                f"km/L_cfg={float(km_l_configurado):.2f} | divergência={float(divergencia)*100:.1f}%"
            )

            # Limiar de 5%: abaixo disso é ruído de medição aceitável
            if divergencia <= Decimal("0.05"):
                return None

            sinal = "▲" if km_l_real > km_l_configurado else "▼"
            return {
                "km_l_real": float(km_l_real),
                "km_l_configurado": float(km_l_configurado),
                "divergencia_pct": round(float(divergencia) * 100, 1),
                "param_nome": param_nome,
                "param_label": param_label,
                "km_intervalo": float(km_intervalo),
                "litros_intervalo": float(litros_intervalo),
                "sinal": sinal,
            }
        except Exception as exc:
            # Falha na recalibração nunca bloqueia o abastecimento — apenas loga.
            logger.warning(f"[Full-to-Full] Erro na recalibração (veiculo={veiculo_id}): {exc}")
            return None

    @staticmethod
    async def estornar_transacao(motorista_id: str, transacao_id: str) -> Dict[str, Any]:
        """Realiza a inversão lógica de lançamento preservando auditoria contábil."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE public.transacoes SET estornado = TRUE 
                    WHERE id = $1::uuid AND motorista_id = $2::uuid AND estornado = FALSE
                    RETURNING id, valor, tipo_movimentacao;
                    """, transacao_id, motorista_id
                )
                if row is None:
                    return {"status": "error", "message": "❌ Lançamento não localizado ou já estornado."}
                
                valor_estornado = f"R$ {float(row['valor']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                tipo = str(row["tipo_movimentacao"]).capitalize()
                return {
                    "status": "success", 
                    "message": f"🔄 Estorno concluído! {tipo} de *{valor_estornado}* anulada com sucesso no cofre."
                }
        except asyncpg.PostgresError as exc:
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def obter_resumo_diario(motorista_id: str, data_iso: str) -> Dict[str, Any]:
        """Agregação SQL para cálculo de saldo líquido com timezone Brasília."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0.0000) AS total_receitas,
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0.0000) AS total_despesas
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid AND estornado = FALSE 
                    AND DATE(data_transacao AT TIME ZONE 'America/Sao_Paulo') = $2::date;
                    """, motorista_id, data_iso
                )
                
                receitas = Decimal(str(row["total_receitas"]))
                despesas = Decimal(str(row["total_despesas"]))
                
                return {
                    "status": "success",
                    "data": data_iso,
                    "financeiro": {
                        "receitas": float(receitas),
                        "despesas": float(despesas),
                        "saldo_liquido": float(receitas - despesas)
                    }
                }
        except Exception as exc:
            logger.error(f"Erro ao obter resumo: {exc}")
            return {"status": "error", "message": f"Falha ao consolidar extrato: {exc}"}
