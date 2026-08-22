class HelpService:
    """
    Provedor estático de informações, tutoriais e documentação de suporte para o motorista.
    Opera de forma stateless, sem alterar o contexto ou o estado da FSM no Redis.
    """
    
    _TEXTOS = {
        "geral": (
            "🤖 *Central de Ajuda CFO Inteligente* 🛡️\n\n"
            "Eu sou o seu CFO Virtual. Entendo comandos em linguagem natural a qualquer momento! "
            "Aqui estão as instruções de uso do sistema:\n\n"
            "🟢 *Para Iniciar Jornada:*\n"
            "Envie: *'Iniciar [KM]'* ou apenas *'Iniciar'*\n"
            "_(Ex: 'Iniciar 13005' ou o bot perguntará seu odômetro)_\n\n"
            "🏁 *Para Encerrar Jornada:*\n"
            "Envie: *'Fechar [KM]'* ou apenas *'Fechar'*\n"
            "_(Ex: 'Fechar 13120' - O DRE diário completo será gerado)_\n\n"
            "⏸️ *Pausas e Intervalos:*\n"
            "Envie: *'Pausa'*, *'Pausar'*, *'Fui Almoçar'* ou *'Retomar'*, *'Voltei'*\n\n"
            "📊 *Resumo Parcial:*\n"
            "Envie: *'Status'*, *'Resumo'* ou *'Parcial'*\n\n"
            "💰 *Lançamentos Livres (Fricção Zero):*\n"
            "• Receitas: *'ganhei 150 na uber'*, *'faturei 80 da 99'*, *'corrida 35 particular'*\n"
            "• Despesas: *'gastei 50 posto'*, *'marmita 22'*, *'paguei 120 mercado'*, *'lava jato 45'*\n\n"
            "Deseja ajuda com um tema específico? Digite:\n"
            "👉 *'Ajuda metas'* - Para entender as metas de faturamento.\n"
            "👉 *'Ajuda contrato'* - Para saber como atualizar aluguel e franquia.\n"
            "👉 *'Ajuda lancamentos'* - Exemplos de registros financeiros."
        ),
        "metas": (
            "🎯 *Ajuda com Metas e Indicadores de Eficiência*\n\n"
            "O CFO Inteligente ajuda você a monitorar sua performance em tempo real com base em metas realistas:\n\n"
            "• *Meta Mensal:* Definida por padrão como *R$ 12.000,00* de faturamento bruto.\n"
            "• *Dias Úteis:* Configurado para *26 dias* de trabalho por mês.\n"
            "• *Meta Diária Recomendada:* O bot calcula automaticamente o valor de *R$ 461,54 por dia trabalhado*.\n\n"
            "Durante a jornada, o sistema audita se seus ganhos parciais estão de acordo com as seguintes métricas:\n"
            "• *Piso de Ganho por KM:* Mínimo de *R$ 2,00 por km rodado*.\n"
            "• *Piso de Ganho por Hora:* Mínimo de *R$ 30,00 por hora trabalhada*.\n\n"
            "Ao fechar o turno, você verá qual percentual da sua meta diária foi atingido! 🚀"
        ),
        "contrato": (
            "⚙️ *Ajuda com Atualização de Contrato (Localiza Zarp, etc.)*\n\n"
            "Se você trocou de carro, mudou de locadora ou o valor do aluguel foi reajustado, você pode atualizar os parâmetros do sistema digitando uma única frase livre:\n\n"
            "👉 *Comando:* _atualizar contrato [Locadora] [Valor Semanal] [Franquia Semanal]_\n"
            "👉 *Exemplo:* _atualizar contrato Zarp 1050 1500_\n\n"
            "O sistema processará as regras contratuais da seguinte forma:\n"
            "• *Custo Fixo Rateado:* Dividirá o valor semanal por 6 (escala padrão de trabalho) para deduzir o aluguel pro-rata diário no seu DRE.\n"
            "• *Franquia de KM Diária:* Dividirá os 1.500 km por 7 dias (214 km/dia) para alertar se você está na média segura de rodagem.\n\n"
            "Se o seu carro for *Próprio* ou *Financiado*, você pode parametrizar a amortização diária (ex: R$ 15,00/dia para custos de depreciação):\n"
            "👉 *Exemplo:* _atualizar contrato Proprietario 90 0_\n"
            "_(R$ 90,00 divididos pela escala de 6 dias úteis resultará em R$ 15,00/dia no DRE)_"
        ),
        "lancamentos": (
            "💰 *Ajuda com Lançamentos Financeiros (Fricção Zero)*\n\n"
            "Você não precisa de comandos rígidos. Escreva exatamente como falaria para um amigo no trânsito:\n\n"
            "🟢 *Registrar Entradas (Ganhos):*\n"
            "• 'ganhei 180 uber'\n"
            "• 'faturei 250 hj na 99'\n"
            "• 'viagem particular de 50 reais'\n"
            "• 'receita de 30 no indrive'\n\n"
            "❌ *Registrar Saídas (Gastos):*\n"
            "• 'gastei 80 no posto de gasolina'\n"
            "• 'paguei 25 de marmita no almoco'\n"
            "• 'lava jato ficou em 45 reais'\n"
            "• 'compras no mercado deu 120'\n"
            "• 'gastei 2 reais de bala para os passageiros'\n\n"
            "🛡️ *Idempotência:* Cada mensagem tem um ID exclusivo. Se o seu sinal cair e o WhatsApp enviar a mesma mensagem duas vezes, o CFO Inteligente bloqueia o segundo registro automaticamente, impedindo faturamentos ou despesas duplicadas no seu caixa!"
        )
    }

    @staticmethod
    def obter_ajuda(topico: str = "geral") -> str:
        """Retorna o texto de ajuda formatado para o tópico correspondente."""
        topico_limpo = topico.lower().strip()
        return HelpService._TEXTOS.get(topico_limpo, HelpService._TEXTOS["geral"])
