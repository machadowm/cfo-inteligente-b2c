"""
HelpService — Ajuda estática por tópico + ajuda contextual baseada no estado da FSM.

v11 — Expansão completa dos textos de ajuda:
  - ajuda metas: explica a diferença entre dias_uteis_mes (divisor de meta) e
    dias_trabalho_previstos (janela de acúmulo das caixinhas), com exemplos numéricos.
  - ajuda parametros: atualizado com todos os comandos implementados (escala, combustivel,
    modelo, placa, nome, despesa única com data, semanal, parcelada).
  - ajuda contrato: vencimento semanal e múltiplo documentados.
  - ajuda lancamentos: uso pessoal fora de turno e auto-resume documentados.
  - ajuda geral: menciona frota, manutenção e perfil.
  - ajuda caixas: novo tópico dedicado ao Sinking Fund.
  - Contextual: adicionado case para CAD_VEICULO_*.
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
            "Comandos essenciais:\n\n"
            "🟢  *iniciar 13005*  → abre o turno com o km do painel\n"
            "🏁  *fechar 13120*  → encerra e gera seu resultado do dia\n"
            "💰  *ganhei 150 uber*  /  *gastei 50 posto*  → lança na hora\n"
            "👤  *perfil*  → Raio-X financeiro do mês\n"
            "📊  *status*  → resumo do turno em andamento\n\n"
            "Tópicos de ajuda:\n"
            "👉  *ajuda metas*         → metas, dias úteis e caixinhas\n"
            "👉  *ajuda contrato*      → aluguel, locadora, escala\n"
            "👉  *ajuda lancamentos*   → como registrar ganhos e gastos\n"
            "👉  *ajuda parametros*    → lista completa de comandos !\n"
            "👉  *ajuda caixas*        → como funcionam as caixinhas\n"
            "👉  *ajuda perfil*        → o que aparece no Raio-X\n\n"
            "Frota:  *!veiculos*  |  *!selecionar ABC1234*\n"
            "Manutenção:  *!manutencao*"
        ),

        "metas": (
            "🎯  *Metas, Dias Úteis e Velocidade de Acúmulo*\n"
            "──────────────────────────────\n\n"
            "Existem  *dois tipos de 'dias'*  no sistema — e confundi-los é a causa mais comum de "
            "caixinha vazia no dia do vencimento.\n\n"
            "1️⃣  *Dias Úteis do Mês*  (`!alterar dias uteis 26`)\n"
            "Define em quantos dias você pretende trabalhar neste mês.\n"
            "O bot usa isso para calcular sua  *meta diária* :\n"
            "  Meta diária = Meta mensal ÷ Dias úteis\n"
            "  Ex: R$ 9.100 ÷ 26 = R$ *350/dia*\n\n"
            "Mais dias úteis → meta diária menor (mais fácil de bater).\n"
            "Menos dias úteis → meta diária maior (mais exigente por turno).\n\n"
            "──────────────────────────────\n\n"
            "2️⃣  *Prazo de Acúmulo*  (segundo número do `!adicionar despesa`)\n"
            "Diz ao bot em quantos turnos você quer ter o dinheiro guardado.\n"
            "  Aporte por turno = Valor da despesa ÷ Prazo de acúmulo\n\n"
            "Exemplo — Seguro de R$ 180, vence dia 26:\n"
            "  `!adicionar despesa seguro 180 26`  → R$ 6,92/turno (prazo folgado)\n"
            "  `!adicionar despesa seguro 180 10`  → R$ 18,00/turno (junta mais rápido)\n\n"
            "⚠️  *Regras de ouro:*\n"
            "• Se você trabalhar  *mais*  do que o prazo, a caixinha enche antes e o bot para de descontar.\n"
            "• Se trabalhar  *menos*, faltará dinheiro no vencimento — você completa do bolso.\n"
            "• Em turnos com lucro negativo, o bot aplica desconto proporcional (não cria saldo fictício).\n\n"
            "──────────────────────────────\n\n"
            "Para ajustar:\n"
            "  `!alterar meta mensal 12000`\n"
            "  `!alterar dias uteis 26`\n"
            "  `!alterar piso km 2,50`  _(mínimo esperado por km rodado)_\n"
            "  `!alterar piso hora 35`  _(mínimo esperado por hora trabalhada)_\n\n"
            "Veja tudo de uma vez:  *!parametros*"
        ),

        "caixas": (
            "📦  *Caixas de Provisão (Sinking Fund)*\n"
            "──────────────────────────────\n\n"
            "Uma caixinha é um  *cofre virtual*  dentro do sistema.\n"
            "A cada turno fechado, uma fatia do seu lucro é reservada automaticamente para pagar contas futuras.\n\n"
            "Como funciona:\n"
            "• Você cadastra uma despesa (ex: seguro R$ 180 vence dia 26).\n"
            "• O bot reserva R$ 6,92 por turno na caixinha 'seguro'.\n"
            "• No dia 26, você retira o valor exato para pagar o boleto.\n\n"
            "Comandos:\n"
            "  `!caixas`                    → ver saldos e barras de progresso\n"
            "  `!criar caixa pneu 500`      → cria caixinha com meta de R$ 500\n"
            "  `!criar caixa reserva`       → cria sem meta (acumulação livre)\n"
            "  `!retirar caixa pneu 480`    → saca quando a conta chegar\n"
            "  `!definir meta caixa pneu 600`  → atualiza a meta\n"
            "  `!excluir caixa pneu`        → remove (só se zerada)\n\n"
            "──────────────────────────────\n\n"
            "Tipos de despesa que alimentam caixinhas:\n\n"
            "📅  *Mensal* — vence todo dia X:\n"
            "  `!adicionar despesa seguro 180 26`\n\n"
            "🔁  *Parcelada* — paga em N vezes:\n"
            "  `!adicionar despesa pneu 800 em 4 parcelas dia 10`\n\n"
            "⚡  *Única* — paga uma vez em data futura:\n"
            "  `!adicionar despesa Manutencao 500 unica 15/10`\n"
            "  _(pro-rata calculado automaticamente pelos dias até o vencimento)_\n\n"
            "📆  *Semanal* — vence toda semana:\n"
            "  `!adicionar despesa semanal diaria 80 5`  _(toda sexta)_\n\n"
            "Para remover:  `!remover despesa seguro`"
        ),

        "contrato": (
            "⚙️  *Atualizar Contrato e Escala*\n"
            "──────────────────────────────\n\n"
            "🚗  *Carro alugado* (Zarp, Movida, Mottu...):\n"
            "  `atualizar contrato Zarp 1050 1500`          ← 6 dias/sem, vence dia 1\n"
            "  `atualizar contrato Movida 900 1200 5`        ← 5 dias/sem\n"
            "  `atualizar contrato Pai 250 0 6 toda terça`  ← semanal, vence toda terça\n"
            "  `atualizar contrato Zarp 1020 1505 6 dia 5 20`  ← vence dias 5 e 20\n\n"
            "🏠  *Carro próprio / financiado*:\n"
            "  `atualizar contrato Proprietario 90`          ← R$ 90/dia\n"
            "  `atualizar contrato Financiado 150`           ← parcela diária\n\n"
            "──────────────────────────────\n\n"
            "Ajustes individuais via !alterar:\n"
            "  `!alterar aluguel 1020,85`\n"
            "  `!alterar franquia 1500`\n"
            "  `!alterar km excedente 0,75`\n"
            "  `!alterar escala seg a sab`   ← atualiza escala e dias/semana\n"
            "  `!alterar dias semana 6`\n\n"
            "Veículos:\n"
            "  `!veiculos`              → lista sua frota\n"
            "  `!selecionar ABC1234`    → troca o veículo ativo\n"
            "  `!alterar modelo HB20`\n"
            "  `!alterar placa ABC1234`\n"
            "  `!alterar combustivel flex`"
        ),

        "perfil": (
            "👤  *Perfil / Raio-X do Mês*\n"
            "──────────────────────────────\n\n"
            "Envie  *Perfil*  (ou  *Meus dados* ) para ver:\n\n"
            "• Turno em andamento (se houver): faturado, custos, ritmo/hora\n"
            "• Estoque virtual do cofre (litros/kWh/m³ com CMP e autonomia)\n"
            "• Alertas de manutenção próximos\n"
            "• Faturamento, despesas e lucro acumulados no mês\n"
            "• Projeção de fechamento do mês\n"
            "• Saldo de caixinhas com prazo de cobertura temporal\n"
            "• Histórico: médias, melhor/pior turno, tendência\n\n"
            "Diferente do  *Status*  (turno em andamento), o Perfil mostra o mês inteiro.\n\n"
            "_Dica: envie  *Perfil*  após cada turno para acompanhar a evolução._"
        ),

        "parametros": (
            "⚙️  *Comandos Rápidos (prefixo !)* \n"
            "──────────────────────────────\n\n"
            "💰  *Metas e pisos:*\n"
            "  `!alterar meta mensal 12000`\n"
            "  `!alterar dias uteis 26`\n"
            "  `!alterar piso km 2,50`\n"
            "  `!alterar piso hora 35`\n"
            "  `!alterar meta horas 8`\n"
            "  `!alterar meta km 250`\n\n"
            "🚗  *Veículo e contrato:*\n"
            "  `!alterar aluguel 1020,85`\n"
            "  `!alterar franquia 1500`\n"
            "  `!alterar km excedente 0,75`\n"
            "  `!alterar escala seg a sab`   _(escala + dias/semana de uma vez)_\n"
            "  `!alterar dias semana 6`\n"
            "  `!alterar modelo HB20`\n"
            "  `!alterar placa ABC1234`\n"
            "  `!alterar combustivel flex`   _(gasolina|etanol|flex|hibrido|eletrico|gnv)_\n"
            "  `!alterar nome social Wil`\n\n"
            "⛽  *Rendimento e cofre de combustível:*\n"
            "  `!alterar km gasolina 12,5`\n"
            "  `!alterar km etanol 9,0`\n"
            "  `!alterar km kwh 6,5`\n"
            "  `!alterar tanque 50`\n"
            "  `!alterar bateria 30`\n"
            "  `!ajustar estoque litros 35`   _(corrigir saldo físico)_\n\n"
            "📌  *Despesas fixas:*\n"
            "  `!adicionar despesa seguro 180 26`              → mensal, vence dia 26\n"
            "  `!adicionar despesa cartao 800 26 5 20`         → vence dias 5 e 20\n"
            "  `!adicionar despesa semanal diaria 80 5`        → toda sexta\n"
            "  `!adicionar despesa pneu 800 em 4 parcelas dia 10`\n"
            "  `!adicionar despesa pneu 800 em 2 parcelas quinzenais`\n"
            "  `!adicionar despesa Manutencao 500 unica 15/10`  → data futura exata\n"
            "  `!despesas fixas`   /   `!remover despesa seguro`\n\n"
            "📦  *Caixinhas:*\n"
            "  `!caixas`  /  `!criar caixa pneu 500`  /  `!retirar caixa pneu 480`\n"
            "  `!excluir caixa pneu`  /  `!definir meta caixa pneu 600`\n\n"
            "🚗  *Frota:*\n"
            "  `!veiculos`  /  `!selecionar ABC1234`  /  `!cadastrar veiculo`\n\n"
            "🔧  *Manutenção:*\n"
            "  `!manutencao`  /  `!manutencao criar troca_oleo 10000`\n\n"
            "📋  *Contrato:*\n"
            "  `atualizar contrato Zarp 1050 1500`\n"
            "  `atualizar contrato Pai 250 0 6 toda terça`\n\n"
            "🛡️  Cada ajuste é salvo com hora e valor anterior."
        ),

        "lancamentos": (
            "💰  *Registrar Ganhos e Gastos*\n"
            "──────────────────────────────\n\n"
            "Escreva como falaria para um amigo:\n\n"
            "🟢  *Ganhos (dentro do turno):*\n"
            "• 'ganhei 180 uber'\n"
            "• 'faturei 250 hoje na 99'\n"
            "• 'corrida particular 50'\n\n"
            "❌  *Gastos (dentro do turno):*\n"
            "• 'gastei 80 no posto'\n"
            "• 'marmita 22'\n"
            "• 'lava jato 45'\n\n"
            "──────────────────────────────\n\n"
            "👤  *Fora do turno — Uso Pessoal:*\n"
            "Você pode registrar ganhos e gastos mesmo sem turno aberto.\n"
            "Eles aparecem separados no Perfil como 'Uso Pessoal' e  *não afetam*  o resultado operacional.\n"
            "• 'mercado 150' (sem turno) → registrado como uso pessoal\n"
            "• 'salário CLT 3000' → idem\n\n"
            "──────────────────────────────\n\n"
            "⛽  *Abastecimento guiado:*\n"
            "• 'abastecer' → bot guia valor, litros, km e tipo de combustível\n"
            "• 'abasteci 80 a 5,85' → registro rápido com preço por litro\n\n"
            "▶️  *Auto-resume:*\n"
            "Se o turno estiver em pausa e você registrar uma receita, o bot retoma automaticamente.\n\n"
            "🛡️  Mensagens duplicadas do WhatsApp são bloqueadas automaticamente."
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

        Lógica de precedência (do mais específico ao mais genérico):
          1. Onboarding ativo                → lembra de completar o cadastro
          2. Cadastro de veículo adicional   → explica passo atual da FSM
          3. Fluxo de abastecimento ativo    → explica passos restantes
          4. Aguardando odômetro inicial     → lembra de enviar km do painel
          5. Aguardando odômetro final       → lembra de enviar km final
          6. Aguardando confirmação de zero  → explica as duas opções
          7. Aguardando declaração de pausa  → explica como declarar ou pular
          8. Turno em pausa                  → mostra comandos de pausa
          9. Turno em andamento/aberto       → mostra comandos in-shift
         10. Sem turno ativo (IDLE/None)     → mostra como iniciar + lançamentos
        """
        st = (estado_turno or "").strip()

        # ── 1. Onboarding incompleto ───────────────────────────────────────────
        if estado_onboard and estado_onboard not in ("IDLE", ""):
            return (
                "📋  *Cadastro em andamento!*\n\n"
                "Você ainda está no meio do seu cadastro inicial. Responda a última pergunta para ativá-lo.\n\n"
                "Se quiser começar do zero, envie  *cancelar* ."
            )

        # ── 2. Cadastro de veículo adicional ──────────────────────────────────
        if st.startswith("CAD_VEICULO_"):
            passo = st.split("|")[0]
            mapa_passo = {
                "CAD_VEICULO_MODELO":      "o  *modelo*  do veículo (ex:  *Honda CG 160* )",
                "CAD_VEICULO_CATEGORIA":   " *Carro*  ou  *Moto* ",
                "CAD_VEICULO_COMBUSTIVEL": "o  *combustível*  (Gasolina, Etanol, Flex, Hibrido, Eletrico, GNV)",
                "CAD_VEICULO_PLACA":       "a  *placa*  do veículo (ex:  *ABC1234* )",
                "CAD_VEICULO_TANQUE":      "a  *capacidade do tanque*  em litros (ex:  *50* )",
                "CAD_VEICULO_BATERIA":     "a  *capacidade da bateria*  em kWh (ex:  *30* )",
            }
            instrucao = mapa_passo.get(passo, "a informação solicitada")
            return (
                f"🚗  *Cadastro de veículo em andamento.*\n\n"
                f"Próximo passo: envie {instrucao}\n\n"
                f"_Para cancelar, envie  *cancelar* ._"
            )

        # ── 3. Fluxo de abastecimento ativo ───────────────────────────────────
        if st.startswith("ABASTECIMENTO_"):
            passo = st.split("|")[0]
            mapa_passo = {
                "ABASTECIMENTO_PRECO":    "o  *preço por litro*  (ex:  *5.89* )",
                "ABASTECIMENTO_LITROS":   "a  *quantidade de litros abastecidos*  (ex:  *35.0* )",
                "ABASTECIMENTO_KM":       "o  *km do odômetro agora*  (ex:  *45230* )",
                "ABASTECIMENTO_CONFIRMA": "Responda  *sim*  para confirmar ou  *não*  para cancelar.",
            }
            instrucao = mapa_passo.get(passo, "a informação solicitada")
            return (
                f"⛽  *Você está no fluxo de abastecimento.*\n\n"
                f"Próximo passo: envie {instrucao}\n\n"
                f"_Para cancelar, envie  *cancelar* ._"
            )

        # ── 4. Aguardando km inicial ───────────────────────────────────────────
        if st == "AGUARDANDO_KM_INICIAL":
            return (
                "🟢  *Abrindo turno!*\n\n"
                "Aguardo o  *número do odômetro*  no painel do seu veículo agora.\n"
                "_(Ex: se o painel mostra 45.230 km, envie  *45230* )_\n\n"
                "_Para cancelar, envie  *cancelar* ._"
            )

        # ── 5. Aguardando km final ─────────────────────────────────────────────
        if st == "AGUARDANDO_KM_FINAL":
            return (
                "🏁  *Encerrando turno!*\n\n"
                "Aguardo o  *número do odômetro final*  no painel agora.\n"
                "_(Ex: se o painel mostra 45.780 km, envie  *45780* )_\n\n"
                "_Para cancelar, envie  *cancelar* ._"
            )

        # ── 6. Aguardando confirmação de faturamento zero ─────────────────────
        if st.startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO"):
            return (
                "⚠  *Confirmação de faturamento zerado pendente.*\n\n"
                "Não encontrei nenhuma receita neste turno. Você tem duas opções:\n\n"
                "✅  *Confirmar*  → fechar o turno mesmo sem lançamentos\n"
                "💰  Envie qualquer valor  (ex:  *ganhei 120 uber* ) → registra e retoma o fechamento\n\n"
                "_Se o dia foi de zero mesmo, confirme. DRE gerado com faturamento R$ 0,00._"
            )

        # ── 7. Aguardando declaração de pausa ─────────────────────────────────
        if st.startswith("AGUARDANDO_DECLARACAO_PAUSA"):
            return (
                "🕐  *Declaração de pausas pendente.*\n\n"
                "Me diz quanto tempo parou para descanso ou refeição:\n\n"
                "• Ex:  *1h30*  ou  *45min*  ou  *2h*\n"
                "• Se não parou, envie  *não*\n\n"
                "_Isso garante que o DRE reflita seu tempo efetivo ao volante._"
            )

        # ── 8. Turno em pausa ─────────────────────────────────────────────────
        if "em_pausa" in st or st == "em_pausa":
            return (
                "⏸  *Seu turno está em PAUSA.*\n\n"
                "Comandos disponíveis:\n\n"
                "▶  *retomar*  (ou  *retomar 45400* ) → volta a rodar\n"
                "🏁  *fechar*  → encerra direto da pausa\n"
                "💰  *ganhei 50*  /  *gastei 25 lanche*  → lança receita ou despesa\n"
                "📊  *status*  → resumo do turno até agora\n\n"
                "_Para ajuda completa, envie  *ajuda geral* ._"
            )

        # ── 9. Turno em andamento ─────────────────────────────────────────────
        if st in ("em_andamento", "ABERTO", "ativo"):
            return (
                "🚗  *Seu turno está ATIVO.*\n\n"
                "Comandos rápidos:\n\n"
                "⏸  *pausar*  (ou  *pausar 45400* ) → pausa para descanso\n"
                "🏁  *fechar 45800*  → encerra e gera o DRE\n"
                "💰  *ganhei 120 uber*  → registra receita\n"
                "💸  *gastei 50 gasolina*  → registra despesa\n"
                "📊  *status*  → resumo parcial do dia\n"
                "👤  *perfil*  → Raio-X do mês completo\n\n"
                "_Para ajuda completa, envie  *ajuda geral* ._"
            )

        # ── 10. Sem turno ativo (IDLE / None) ────────────────────────────────
        return (
            "😴  *Nenhum turno aberto no momento.*\n\n"
            "Quando estiver pronto:\n\n"
            "🟢  *iniciar 45230*  → abre o turno com km do painel\n"
            "🟢  *iniciar*  → o bot pergunta o km\n\n"
            "Outros comandos disponíveis:\n"
            "👤  *perfil*  → Raio-X financeiro do mês\n"
            "⚙️  *!parametros*  → ajusta metas e contrato\n"
            "📦  *!caixas*  → saldo das caixinhas\n"
            "🚗  *!veiculos*  → sua frota\n\n"
            "_Para ver todos os comandos, envie  *ajuda geral* ._"
        )
