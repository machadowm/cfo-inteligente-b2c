"""
HelpService — Ajuda estática por tópico + ajuda contextual baseada no estado da FSM.

Melhorias v9:
  - `obter_ajuda_contextual(estado_turno, estado_onboard)` — retorna uma mensagem
    enxuta e relevante ao momento exato do motorista, em vez do menu completo genérico.
  - `obter_ajuda(topico)` — mantido intacto para compatibilidade com chamadas diretas
    do tipo "ajuda metas", "ajuda contrato", etc.

Regras de contexto FSM:
  IDLE / None        → Motor desligado: mostra como iniciar turno + lançamentos
  AGUARDANDO_KM_*    → Aguardando odômetro: lembra qual valor enviar
  ABASTECIMENTO_*    → Fluxo de abastecimento ativo: lembra como cancelar ou avançar
  em_andamento/ABERTO → Turno rodando: mostra comandos in-shift
  em_pausa           → Turno pausado: mostra retomada, lançamentos, fechar
  AGUARDANDO_CONF*   → Esperando confirmação de zero receita: explica as opções
  Onboarding ativo   → Explica que o cadastro está em andamento
"""


class HelpService:
    """
    Provedor de informações, tutoriais e documentação de suporte para o motorista.

    Métodos:
      obter_ajuda(topico)                              → ajuda estática por tópico
      obter_ajuda_contextual(estado_turno, estado_onboard) → ajuda dinâmica por FSM
    """

    _TEXTOS = {
        "geral": (
            "🤖  *Central de Ajuda — Parceiro do Painel*  🛡️\n\n"
            "Aqui estão os 3 comandos mais usados:\n\n"
            "🟢  *iniciar 13005*  → abre o turno com o KM do painel\n"
            "🏁  *fechar 13120*  → encerra e gera seu resultado do dia\n"
            "💰  *ganhei 150 uber*  /  *gastei 50 posto*  → lança na hora\n\n"
            "Precisa de mais detalhe? Escolha um tema:\n"
            "👉  *ajuda metas*   👉  *ajuda contrato*   👉  *ajuda lancamentos*\n"
            "👉  *ajuda parametros*   👉  *ajuda perfil*"
        ),
        "metas": (
            "🎯  *Metas e Performance* \n\n"
            "O bot monitora se você está no ritmo certo para bater sua meta mensal.\n\n"
            "Para ajustar:\n"
            "  👉  *!alterar meta mensal 15000*\n"
            "  👉  *!alterar dias uteis 22*\n\n"
            "🚦  *Alertas de piso* (aparecem no fechamento):\n"
            "  👉  *!alterar piso km 2,50*    _(mínimo que espera ganhar por km)_\n"
            "  👉  *!alterar piso hora 35*    _(mínimo por hora trabalhada)_\n\n"
            "Envie  *!parametros*  para ver tudo de uma vez."
        ),
        "contrato": (
            "⚙️  *Atualizar Contrato* \n\n"
            "Se mudou de carro, locadora ou valor do aluguel:\n\n"
            "🚗  *Carro alugado* (Zarp, Movida, Mottu...):\n"
            "  👉  *atualizar contrato Zarp 1050 1500*       ← 6 dias/sem\n"
            "  👉  *atualizar contrato Movida 900 1200 5*    ← 5 dias/sem\n\n"
            "🏠  *Carro próprio / financiado*:\n"
            "  👉  *atualizar contrato Proprietario 90*      ← R$ 90/dia\n\n"
            "_O KM do painel inicial e final é pedido a cada turno. "
            "A franquia diária é calculada automaticamente (km semanal ÷ 7)._"
        ),
        "perfil": (
            "👤  *Perfil / Raio-X do Mês* \n\n"
            "Envie  *Perfil*  (ou  *Meus dados* ) para ver:\n\n"
            "• Lucro real acumulado no mês\n"
            "• Quanto falta para a meta\n"
            "• Projeção de faturamento\n"
            "• Dinheiro guardado nas caixinhas\n"
            "• Histórico recente de turnos\n\n"
            "_Diferente do *Status* (turno em andamento), o Perfil mostra o mês inteiro._"
        ),
        "parametros": (
            "⚙️  *Comandos Rápidos (prefixo !)* \n\n"
            "💰  *Metas e pisos:*\n"
            "  *!alterar meta mensal 12000*\n"
            "  *!alterar piso km 2,50*\n\n"
            "🚗  *Contrato:*\n"
            "  *!alterar aluguel 1020,85*\n"
            "  *!alterar franquia 1500*\n\n"
            "⛽  *Rendimento real do veículo:*\n"
            "  *!alterar km gasolina 12,5*\n\n"
            "🔧  *Corrigir KM do cofre:*\n"
            "  *!ajustar estoque litros 35*\n\n"
            "📌  *Despesas mensais recorrentes:*\n"
            "  *!adicionar despesa seguro 180 26*          → R$ 180 todo dia 26\n"
            "  *!adicionar despesa seguro 1200 em 4 parcelas todo dia 10*\n"
            "  *!adicionar despesa pneu 800 em 2 parcelas quinzenais*\n"
            "  *!adicionar despesa manutencao 500 unica dia 15* → paga uma vez\n"
            "  *!despesas fixas*  /  *!remover despesa seguro*\n\n"
            "📦  *Caixinhas (dinheiro guardado para cada conta):*\n"
            "  *!caixas*                    → ver saldos\n"
            "  *!criar caixa pneu 500*      → cria caixinha com meta\n"
            "  *!retirar caixa pneu 480*    → sacar quando a conta chegar\n\n"
            "🛡️  Cada ajuste é salvo com hora e valor anterior."
        ),
        "lancamentos": (
            "💰  *Registrar Ganhos e Gastos (sem comando fixo)* \n\n"
            "Escreva como falaria para um amigo:\n\n"
            "🟢  *Ganhos:*\n"
            "• 'ganhei 180 uber'\n"
            "• 'faturei 250 hoje na 99'\n"
            "• 'corrida particular 50'\n\n"
            "❌  *Gastos:*\n"
            "• 'gastei 80 no posto'\n"
            "• 'marmita 22'\n"
            "• 'lava jato 45'\n\n"
            "🛡️  Se o WhatsApp enviar a mesma mensagem duas vezes, o bot bloqueia automaticamente — sem lançamento duplicado."
        ),
    }

    @staticmethod
    def obter_ajuda(topico: str = "geral") -> str:
        """Retorna o texto de ajuda formatado para o tópico correspondente."""
        topico_limpo = topico.lower().strip()
        return HelpService._TEXTOS.get(topico_limpo, HelpService._TEXTOS["geral"])

    @staticmethod
    def obter_ajuda_contextual(estado_turno: str | None, estado_onboard: str | None = None) -> str:
        """Retorna ajuda enxuta e relevante ao estado atual da FSM do motorista.

        Parâmetros:
            estado_turno   — Valor lido do Redis para a chave `turno_flow:<tenant_id>`.
                             Pode ser None/IDLE (sem turno) ou um estado serializado
                             (ex: "ABASTECIMENTO_PRECO|litros:35.0").
            estado_onboard — Valor lido do Redis para a chave `onboard:<tenant_id>`.
                             Passado quando o motorista ainda não completou o cadastro.

        Lógica de precedência (do mais específico ao mais genérico):
          1. Onboarding ativo                → lembra de completar o cadastro
          2. Fluxo de abastecimento ativo    → explica passos restantes e como cancelar
          3. Aguardando odômetro inicial     → lembra de enviar km do painel
          4. Aguardando odômetro final       → lembra de enviar km final
          5. Aguardando confirmação de zero  → explica as duas opções disponíveis
          6. Aguardando declaração de pausa  → explica como declarar ou pular
          7. Turno em pausa                  → mostra comandos de pausa
          8. Turno em andamento/aberto       → mostra comandos in-shift
          9. Sem turno ativo (IDLE/None)     → mostra como iniciar + lançamentos
        """
        st = (estado_turno or "").strip()

        # ── 1. Onboarding incompleto ───────────────────────────────────────────
        if estado_onboard and estado_onboard not in ("IDLE", ""):
            return (
                "📋  *Cadastro em andamento!* \n\n"
                "Você ainda está no meio do seu cadastro inicial. Responda a última pergunta que te fiz para ativá-lo.\n\n"
                "Se quiser começar do zero, envie  *cancelar* ."
            )

        # ── 2. Fluxo de abastecimento ativo ───────────────────────────────────
        if st.startswith("ABASTECIMENTO_"):
            passo = st.split("|")[0]
            mapa_passo = {
                "ABASTECIMENTO_PRECO":    "o  *preço por litro*  (ex:  *5.89* )",
                "ABASTECIMENTO_LITROS":   "a  *quantidade de litros abastecidos*  (ex:  *35.0* )",
                "ABASTECIMENTO_KM":       "o  *km do odômetro agora*  (ex:  *45230* )",
                "ABASTECIMENTO_CONFIRMA": "Responda  *sim*  para confirmar o abastecimento ou  *não*  para cancelar.",
            }
            instrucao = mapa_passo.get(passo, "a informação solicitada")
            return (
                f"⛽  *Você está no fluxo de abastecimento.* \n\n"
                f"Próximo passo: envie {instrucao}\n\n"
                f"_Para cancelar o abastecimento, envie  *cancelar* ._"
            )

        # ── 3. Aguardando km inicial ───────────────────────────────────────────
        if st == "AGUARDANDO_KM_INICIAL":
            return (
                "🟢  *Abrindo turno!* \n\n"
                "Aguardo o  *número do odômetro*  no painel do seu veículo agora.\n"
                "_(Ex: se o painel mostra 45.230 km, envie  *45230* )_\n\n"
                "_Para cancelar, envie  *cancelar* ._"
            )

        # ── 4. Aguardando km final ─────────────────────────────────────────────
        if st == "AGUARDANDO_KM_FINAL":
            return (
                "🏁  *Encerrando turno!* \n\n"
                "Aguardo o  *número do odômetro final*  no painel agora.\n"
                "_(Ex: se o painel mostra 45.780 km, envie  *45780* )_\n\n"
                "_Para cancelar, envie  *cancelar* ._"
            )

        # ── 5. Aguardando confirmação de faturamento zero ─────────────────────
        if st.startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO"):
            return (
                "⚠  *Confirmação de faturamento zerado pendente.* \n\n"
                "Não encontrei nenhuma receita neste turno. Você tem duas opções:\n\n"
                "✅  *Confirmar*  → fechar o turno mesmo sem lançamentos\n"
                "💰  Envie qualquer valor  (ex:  *ganhei 120 uber* ) → registra a receita e retoma o fechamento\n\n"
                "_Se o dia foi de zero mesmo, confirme. Seu DRE será gerado com faturamento R$ 0,00._"
            )

        # ── 6. Aguardando declaração de pausa ─────────────────────────────────
        if st.startswith("AGUARDANDO_DECLARACAO_PAUSA"):
            return (
                "🕐  *Declaração de pausas pendente.* \n\n"
                "Você trabalhou bastante hoje! Me diz quanto tempo parou para descanso ou refeição:\n\n"
                "• Ex:  *1h30*  ou  *45min*  ou  *2h* \n"
                "• Se não parou, envie  *não* \n\n"
                "_Isso garante que o DRE reflita seu tempo efetivo ao volante._"
            )

        # ── 7. Turno em pausa ─────────────────────────────────────────────────
        if "em_pausa" in st or st == "em_pausa":
            return (
                "⏸  *Seu turno está em PAUSA.* \n\n"
                "Comandos disponíveis agora:\n\n"
                "▶  *retomar*  (ou  *retomar 45400* ) → volta a rodar\n"
                "🏁  *fechar*  → encerra direto da pausa (solicita km final)\n"
                "💰  *ganhei 50*  /  *gastei 25 lanche*  → lança receita ou despesa\n"
                "📊  *status*  → vê o resumo do turno até agora\n\n"
                "_Para ajuda completa, envie  *ajuda geral* ._"
            )

        # ── 8. Turno em andamento ─────────────────────────────────────────────
        # O estado real do turno no DB é 'em_andamento' ou 'ABERTO'.
        # A FSM Redis guarda None/IDLE quando não há fluxo guiado ativo —
        # verificamos o DB via status_db injetado pelo orquestrador.
        if st in ("em_andamento", "ABERTO", "ativo"):
            return (
                "🚗  *Seu turno está ATIVO.* \n\n"
                "Comandos rápidos:\n\n"
                "⏸  *pausar*  (ou  *pausar 45400* ) → pausa para descanso\n"
                "🏁  *fechar 45800*  → encerra o expediente e gera o DRE\n"
                "💰  *ganhei 120 uber*  → registra receita instantânea\n"
                "💸  *gastei 50 gasolina*  → registra despesa\n"
                "📊  *status*  → resumo parcial do dia\n"
                "👤  *perfil*  → Raio-X do mês completo\n\n"
                "_Para ajuda completa, envie  *ajuda geral* ._"
            )

        # ── 9. Sem turno ativo (IDLE / None) ─────────────────────────────────
        return (
            "😴  *Nenhum turno aberto no momento.* \n\n"
            "Quando estiver pronto para trabalhar:\n\n"
            "🟢  *iniciar 45230*  → abre o turno com km do painel\n"
            "🟢  *iniciar*  → o bot pergunta o km\n\n"
            "Outros comandos disponíveis agora:\n"
            "👤  *perfil*  → Raio-X financeiro do mês\n"
            "📊  *status*  → verifica se há turno aberto\n"
            "⚙️  *!parametros*  → ajusta metas e contrato\n\n"
            "_Para ver todos os comandos, envie  *ajuda geral* ._"
        )
