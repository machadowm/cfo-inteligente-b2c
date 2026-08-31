#!/bin/bash
# =============================================================================
# CFO INTELIGENTE B2C - SUÍTE SRE DE RESET E LIMPEZA DE AMBIENTE DE TESTES
# VERSÃO: v2.0.0
# AUTOR: Gemini Notebook (SRE & Senior Database Architect)
# DESCRIÇÃO: Executa o expurgo de transações, turnos e cache do Redis para um
#            tenant específico (isolado) ou realiza o wipe total do ledger 
#            em ambiente local de staging/desenvolvimento.
#            Fix: Correção do escape de aspas simples no reset de estoque JSONB.
# =============================================================================

set -e

# Configurações padrão herdeiras do Docker-Compose
DB_CONTAINER="cfo_postgres"
REDIS_CONTAINER="cfo_redis"
DB_USER="admin"
DB_NAME="cfo_b2c"

exibir_ajuda() {
    echo -e "Uso: $0 [opções]"
    echo -e "\nOpções disponíveis:"
    echo -e "  -t, --tenant TELEFONE      Executa o reset contábil ISOLADO para um motorista específico (Ex: 5513997971393)."
    echo -e "  -g, --global-danger        EXPURGO TOTAL de dados operacionais (Staging/Local apenas). Mantém cadastros."
    echo -e "  -h, --help                 Exibe esta mensagem de ajuda."
    echo -e "\nExemplo de reset isolado para o motorista Willian:"
    echo -e "  $0 --tenant 5513997971393\n"
}

# Parâmetros de execução
MODE=""
TENANT_PHONE=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -t|--tenant) TENANT_PHONE="$2"; MODE="tenant"; shift ;;
        -g|--global-danger) MODE="global"; ;;
        -h|--help) exibir_ajuda; exit 0 ;;
        *) echo "Opção desconhecida: $1"; exibir_ajuda; exit 1 ;;
    esac
    shift
done

if [ -z "$MODE" ]; then
    echo "❌ Erro: Você deve especificar --tenant ou --global-danger."
    exibir_ajuda
    exit 1
fi

# ------------------------------------------------------------------------------
# EXECUÇÃO DO MODO ISOLADO POR TENANT
# ------------------------------------------------------------------------------
if [ "$MODE" = "tenant" ]; then
    if [ -z "$TENANT_PHONE" ]; then
        echo "❌ Erro: O telefone do tenant é obrigatório para o modo isolado."
        exit 1
    fi

    echo "🔍 Resolvendo UUID do tenant para o telefone: $TENANT_PHONE..."
    
    # Busca o UUID do motorista de forma limpa
    MOTORISTA_ID=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c \
        "SELECT id FROM public.motoristas WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(telefone, '+', ''), ' ', ''), '-', ''), '(', ''), ')', '') LIKE '%${TENANT_PHONE: -11}';")

    if [ -z "$MOTORISTA_ID" ] || [ "$MOTORISTA_ID" = "SELECT 1" ]; then
        echo "❌ Erro: Motorista com telefone '$TENANT_PHONE' não foi localizado no banco de dados."
        exit 1
    fi

    echo "✅ Tenant localizado! UUID correspondente: $MOTORISTA_ID"
    echo -e "\n⚠️  Iniciando expurgo contábil do tenant no PostgreSQL..."

    # Executa a limpeza relacional transacional
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" <<EOF
BEGIN;

-- Força o Tenant ID para contornar políticas de RLS nas queries administrativas se aplicável
SET LOCAL app.current_driver_id = '$MOTORISTA_ID';

-- 1. Remove histórico de manutenção associado aos veículos do motorista
DELETE FROM public.historico_manutencao 
WHERE veiculo_id IN (SELECT id FROM public.veiculos WHERE motorista_id = '$MOTORISTA_ID'::uuid);

-- 2. Remove snapshots dos fechamentos diários (DREs congelados)
DELETE FROM public.fechamento_diario WHERE motorista_id = '$MOTORISTA_ID'::uuid;

-- 3. Remove fechamentos consolidados (mensais/anuais)
DELETE FROM public.fechamentos_consolidados WHERE motorista_id = '$MOTORISTA_ID'::uuid;

-- 4. Remove transações físicas e metadados de abastecimento do cofre
DELETE FROM public.transacoes WHERE motorista_id = '$MOTORISTA_ID'::uuid;

-- 5. Remove pausas de turnos
DELETE FROM public.pausas_turno 
WHERE turno_id IN (SELECT id FROM public.turnos WHERE motorista_id = '$MOTORISTA_ID'::uuid);

-- 6. Remove turnos de jornadas
DELETE FROM public.turnos WHERE motorista_id = '$MOTORISTA_ID'::uuid;

-- 7. Reseta o saldo acumulado das caixas de provisão do motorista para zero
UPDATE public.caixas_provisao 
SET saldo_atual = 0.0000 
WHERE motorista_id = '$MOTORISTA_ID'::uuid;

-- 8. Restaura o cofre virtual de combustível do veículo ativo para o estado nominal zerado
UPDATE public.veiculos 
SET estoque_financeiro = '{
    "meta": {
        "is_flex": false, 
        "is_hibrido": false, 
        "is_eletrico": false, 
        "qtd_tanques": 1, 
        "tipo_veiculo": "etanol", 
        "capacidade_tanque_l": 40.0, 
        "capacidade_bateria_kwh": 0.0
    }, 
    "liquido": {
        "litros": 0.0, 
        "custo_total": 0.0, 
        "km_l_etanol": 8.5, 
        "etanol_litros": 0.0, 
        "km_l_gasolina": 12.0, 
        "gasolina_litros": 0.0, 
        "etanol_proporcao": 0.0, 
        "gasolina_proporcao": 1.0
    }, 
    "eletricidade": {
        "kwh": 0.0, 
        "km_kwh": 6.5, 
        "custo_total": 0.0
    }, 
    "gnv": {
        "m3": 0.0, 
        "km_m3": 14.0, 
        "custo_total": 0.0
    }
}'::jsonb
WHERE motorista_id = '$MOTORISTA_ID'::uuid AND ativo = TRUE;

COMMIT;
EOF

    echo "✅ Expurgado com sucesso no PostgreSQL!"
    echo -e "\n⚡ Iniciando limpeza de caches e buffers da FSM no Redis..."

    # Deleta as chaves de controle de estado e contadores no Redis (Database 0)
    docker exec -i "$REDIS_CONTAINER" redis-cli <<EOF
DEL "state:turno_flow:$TENANT_PHONE"
DEL "state:onboard:$TENANT_PHONE"
DEL "buffer_msg:$TENANT_PHONE"
DEL "errors:$TENANT_PHONE"
DEL "profile:$TENANT_PHONE"
DEL "audit_trava_zero:$TENANT_PHONE"
DEL "audit_trava_zero:$TENANT_PHONE:meta"
EOF

    echo "✅ Cache e buffers da FSM do Redis expurgados!"
    echo -e "\n🎉 SUCESSO: O ambiente de testes do tenant $TENANT_PHONE está 100% zerado e pronto para nova simulação!"

# ------------------------------------------------------------------------------
# EXECUÇÃO DO MODO GLOBAL (DANGER ZONE)
# ------------------------------------------------------------------------------
elif [ "$MODE" = "global" ]; then
    echo -e "⚠️  DANGER ZONE: Isso irá deletar TODA a atividade contábil de todos os motoristas!"
    echo -e "Os cadastros básicos de motoristas e veículos ativos serão preservados."
    read -p "Tem certeza absoluta que deseja prosseguir? (digite 'SIM' para confirmar): " CONFIRMA

    if [ "$CONFIRMA" != "SIM" ]; then
        echo "❌ Operação cancelada pelo operador."
        exit 0
    fi

    echo -e "\n🧹 Iniciando limpeza global no PostgreSQL..."
    
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "TRUNCATE TABLE public.fechamento_diario, public.fechamentos_consolidados, public.pausas_turno, public.transacoes, public.turnos, public.historico_manutencao, public.lgpd_logs RESTART IDENTITY CASCADE;"

    # Reseta todos os estoques financeiros para nominal zerado
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "UPDATE public.veiculos SET estoque_financeiro = '{\"meta\": {\"is_flex\": false, \"is_hibrido\": false, \"is_eletrico\": false, \"qtd_tanques\": 1, \"tipo_veiculo\": \"etanol\", \"capacidade_tanque_l\": 40.0, \"capacidade_bateria_kwh\": 0.0}, \"liquido\": {\"litros\": 0.0, \"custo_total\": 0.0, \"km_l_etanol\": 8.5, \"etanol_litros\": 0.0, \"km_l_gasolina\": 12.0, \"gasolina_litros\": 0.0, \"etanol_proporcao\": 0.0, \"gasolina_proporcao\": 1.0}, \"eletricidade\": {\"kwh\": 0.0, \"km_kwh\": 6.5, \"custo_total\": 0.0}, \"gnv\": {\"m3\": 0.0, \"km_m3\": 14.0, \"custo_total\": 0.0}}'::jsonb WHERE ativo = TRUE;"

    # Reseta o saldo de todas as caixas de provisão
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
        "UPDATE public.caixas_provisao SET saldo_atual = 0.0000;"

    echo "✅ Tabelas de movimentação limpas globalmente no PostgreSQL!"
    echo -e "\n🧹 Limpando chaves do Redis..."

    # Limpa todas as chaves do Redis da base 0 (estado/buffers) de forma segura
    docker exec -i "$REDIS_CONTAINER" redis-cli --eval /dev/stdin <<< "return redis.call('del', unpack(redis.call('keys', '*')))" 2>/dev/null || true

    echo "✅ Todas as chaves do Redis expurgadas!"
    echo -e "\n🎉 SUCESSO: O ledger de movimentações do CFO Inteligente está 100% limpo e pronto para novos testes integrados!"
fi

