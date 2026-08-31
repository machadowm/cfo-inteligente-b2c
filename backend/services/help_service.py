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
            "Eu sou o seu Parceiro do Painel. Entendo comandos em linguagem natural a qualquer momento! "
            "Aqui estão as instruções de uso do sistema:\n\n"
            "🟢  *Para Iniciar Jornada:*\n"
            "Envie:  *'Iniciar [KM]'*  ou apenas  *'Iniciar'*\n"
            "_(Ex: 'Iniciar 13005' ou o bot perguntará seu odômetro)_\n\n"
            "🏁  *Para Encerrar Jornada:*\n"
            "Envie:  *'Fechar [KM]'*  ou apenas  *'Fechar'*\n"
            "_(Ex: 'Fechar 13120' - O DRE diário completo será gerado)_\n\n"
            "⏸️  *Pausas e Intervalos:*\n"
            "Envie:  *'Pausa'* ,  *'Pausar'* ,  *'Fui Almoçar'*  ou  *'Retomar'* ,  *'Voltei'* \n\n"
            "📊  *Resumo Parcial:*\n"
            "Envie:  *'Status'* ,  *'Resumo'*  ou  *'Parcial'* \n\n"
            "💰  *Lançamentos Livres (Fricção Zero):*\n"
            "• Receitas:  *'ganhei 150 na uber'* ,  *'faturei 80 da 99'* ,  *'corrida 35 particular'* \n"
            "• Despesas:  *'gastei 50 posto'* ,  *'marmita 22'* ,  *'paguei 120 mercado'* ,  *'lava jato 45'* \n\n"
            "Deseja ajuda com um tema específico? Digite:\n"
            "👉  *'Ajuda metas'*  - Para entender as metas de faturamento.\n"
            "👉  *'Ajuda contrato'*  - Para saber como atualizar aluguel e franquia.\n"
            "👉  *'Ajuda lancamentos'*  - Exemplos de registros financeiros.\n"
            "👉  *'Ajuda parametros'*  - Comandos rápidos com  *!*  para ajuste de parâmetros.\n"
            "👉  *'Ajuda perfil'*  - Raio-X completo: metas, estoque, histórico do mês."
        ),
        "metas": (
            "🎯  *Ajuda com Metas e Indicadores de Eficiência* \n\n"
            "O Parceiro do Painel monitora sua performance em tempo real com base nos parâmetros que você mesmo define:\n\n"
            "📊  *Meta de Faturamento:*\n"
            "•  *Meta Mensal:*  Valor alvo de receita bruta por mês.\n"
            "•  *Dias Úteis:*  Quantos dias você planeja trabalhar no mês.\n"
            "•  *Meta Diária:*  Calculada automaticamente como  _meta mensal ÷ dias úteis_ .\n\n"
            "Para alterar, envie:\n"
            "  👉  *!alterar meta mensal 15000*\n"
            "  👉  *!alterar dias uteis 22*\n\n"
            "🚦  *Pisos Mínimos de Performance (alertas reais):*\n"
            "Ao fechar o turno, o sistema compara seu faturamento real com dois pisos:\n"
            "•  *Piso por KM:*  Faturamento bruto mínimo esperado por km rodado.\n"
            "•  *Piso por Hora:*  Faturamento bruto mínimo esperado por hora trabalhada.\n\n"
            "Se ficar abaixo de qualquer piso, você recebe um alerta no DRE imediatamente.\n\n"
            "Para alterar os pisos, envie:\n"
            "  👉  *!alterar piso km 2,50*    _(padrão: R$ 2,00/km)_\n"
            "  👉  *!alterar piso hora 35*    _(padrão: R$ 30,00/h)_\n\n"
            "📈  *O que você vê no DRE ao fechar o turno:*\n"
            "• Percentual de atingimento da meta diária\n"
            "• Alertas de piso (quando o ganho/km ou ganho/hora estiver abaixo do seu limite)\n"
            "• Projeção de faturamento mensal se mantiver o ritmo do dia\n\n"
            "Envie  *!parametros*  para ver todos os parâmetros ajustáveis. 🚀"
        ),
        "contrato": (
            "⚙️  *Ajuda com Atualização de Contrato (Localiza Zarp, etc.)* \n\n"
            "Se você trocou de carro, mudou de locadora ou o valor do aluguel foi reajustado, você pode atualizar os parâmetros do sistema digitando uma única frase livre:\n\n"
            "👉  *Comando:*  _atualizar contrato [Locadora] [Valor Semanal] [Franquia Semanal]_\n"
            "👉  *Exemplo:*  _atualizar contrato Zarp 1050 1500_\n\n"
            "O sistema processará as regras contratuais da seguinte forma:\n"
            "•  *Custo Fixo Rateado:*  Dividirá o valor semanal por 6 (escala padrão de trabalho) para deduzir o aluguel pro-rata diário no seu DRE.\n"
            "•  *Franquia de KM Diária:*  Dividirá os 1.500 km por 7 dias (214 km/dia) para alertar se você está na média segura de rodagem.\n\n"
            "Se o seu carro for  *Próprio*  ou  *Financiado* , você pode parametrizar a amortização diária (ex: R$ 15,00/dia para custos de depreciação):\n"
            "👉  *Exemplo:*  _atualizar contrato Proprietario 90 0_\n"
            "_(R$ 90,00 divididos pela escala de 6 dias úteis resultará em R$ 15,00/dia no DRE)_"
        ),
        "perfil": (
            "👤  *Ajuda — Raio-X do Motorista (Perfil Completo)* \n\n"
            "O comando  *Perfil*  (ou  *Meus Dados* ) exibe um relatório completo fora do turno:\n\n"
            "📋  *O que você verá:*\n"
            "• Dados do veículo ativo e contrato vigente\n"
            "• Estoque virtual de combustível/energia no cofre (com CMP)\n"
            "• Receitas, despesas e lucro real acumulados no mês\n"
            "• Barra de progresso da meta mensal\n"
            "• Projeção de faturamento até o fim do mês\n"
            "• Histórico: km médio, faturamento, custo e lucro médio por dia\n"
            "• Alerta de ritmo quando últimos turnos estão abaixo da meta\n\n"
            "💬  *Como usar:*\n"
            "Envie qualquer uma destas palavras:\n"
            "  *Perfil*  |  *Meus dados*  |  *Minha conta*  |  *Raio X* \n\n"
            "🛡️  *Diferença para o Status:*\n"
            "• O  *Status*  foca no turno em andamento (km inicial, abastecimento do dia).\n"
            "• O  *Perfil*  consolida o histórico do mês inteiro e as configurações do sistema."
        ),
        "parametros": (
            "⚙️  *Ajuda — Comandos Administrativos (prefixo !)* \n\n"
            "Ajuste qualquer parâmetro a qualquer hora, sem abrir menu:\n\n"
            "💰  *Financeiro:*\n"
            "  *!alterar meta mensal 12000*\n"
            "  *!alterar dias uteis 26*\n"
            "  *!alterar piso km 2,50*          → piso de faturamento por km rodado\n"
            "  *!alterar piso hora 35*           → piso de faturamento por hora trabalhada\n\n"
            "🚗  *Contrato e Veículo:*\n"
            "  *!alterar aluguel 1020,85*       → custo semanal do contrato\n"
            "  *!alterar franquia 1500*          → km inclusos por semana\n"
            "  *!alterar km excedente 0,75*      → preço de cada km extra\n"
            "  *!alterar tanque 50*              → capacidade do tanque em litros\n"
            "  *!alterar bateria 40*             → capacidade da bateria em kWh\n\n"
            "⛽  *Rendimento (consumo real do veículo):*\n"
            "  *!alterar km gasolina 12,5*\n"
            "  *!alterar km etanol 9,0*\n"
            "  *!alterar km kwh 6,5*\n"
            "  *!alterar km m3 14,0*\n\n"
            "🔧  *Corrigir saldo do cofre virtual:*\n"
            "  *!ajustar estoque litros 35*\n"
            "  *!ajustar estoque kwh 20*\n"
            "  *!ajustar estoque m3 8*\n\n"
            "📌  *Despesas fixas mensais (seguro, internet, manutenção...):*\n"
            "  *!despesas fixas*                 → listar despesas ativas\n"
            "  *!adicionar despesa seguro 180 26* → R$ 180/mês ÷ 26 dias = R$ 6,92/dia no DRE\n"
            "  *!remover despesa seguro*          → desativar\n\n"
            "📦  *Caixas de Provisão (sinking fund — reserva com meta):*\n"
            "  *!caixas*                          → ver saldos, barras de progresso e previsão\n"
            "  *!criar caixa pneu 500*            → cria caixa com meta de R$ 500\n"
            "  *!criar caixa viagem*              → cria sem meta (acumula livremente)\n"
            "  *!definir meta caixa pneu 500*     → define ou altera a meta de uma caixa\n"
            "  *!remover meta caixa pneu*         → remove o teto (volta a acumular sem limite)\n"
            "  *!retirar caixa pneu 480*          → sacar quando a despesa real chegar\n\n"
            "_A cada fechamento de turno, o pro-rata da despesa fixa vinculada é creditado_\n"
            "_automaticamente. Quando o saldo atingir a meta, o aporte para — igual a_\n"
            "_uma conta de condomínio que você mesmo controla._\n\n"
            "📂 Lista completa com descrição:\n"
            "  *!parametros*\n\n"
            "🛡️  *Segurança:* Cada ajuste é registrado com hora e valor anterior para auditoria."
        ),
        "lancamentos": (
            "💰  *Ajuda com Lançamentos Financeiros (Fricção Zero)* \n\n"
            "Você não precisa de comandos rígidos. Escreva exatamente como falaria para um amigo no trânsito:\n\n"
            "🟢  *Registrar Entradas (Ganhos):*\n"
            "• 'ganhei 180 uber'\n"
            "• 'faturei 250 hj na 99'\n"
            "• 'viagem particular de 50 reais'\n"
            "• 'receita de 30 no indrive'\n\n"
            "❌  *Registrar Saídas (Gastos):*\n"
            "• 'gastei 80 no posto de gasolina'\n"
            "• 'paguei 25 de marmita no almoco'\n"
            "• 'lava jato ficou em 45 reais'\n"
            "• 'compras no mercado deu 120'\n"
            "• 'gastei 2 reais de bala para os passageiros'\n\n"
            "🛡️  *Idempotência:* Cada mensagem tem um ID exclusivo. Se o seu sinal cair e o WhatsApp enviar a mesma mensagem duas vezes, o Parceiro do Painel bloqueia o segundo registro automaticamente, impedindo faturamentos ou despesas duplicadas no seu caixa!"
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
