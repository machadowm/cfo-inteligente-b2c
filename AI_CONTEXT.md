# AI CONTEXT & ARCHITECTURAL BLUEPRINT - CFO INTELIGENTE B2C

## 1. Visão Geral e Propósito
SaaS financeiro e ERP logístico conversacional voltado para motoristas de aplicativo (Uber, 99, Mottu) e frotistas autônomos. Opera com fricção zero via WhatsApp, consolidando contabilidade diária, DRE executivo, controle de frotas multi-energia e provisões.

## 2. Pilha Tecnológica
- **Infraestrutura**: On-Premise / Bare-Metal (Ubuntu Server 26.04 LTS em VM Hyper-V).
- **Orquestração**: Docker Compose.
- **Banco de Dados**: PostgreSQL 15 com isolamento estrito de Multi-Tenancy via Row-Level Security (RLS) usando `SET LOCAL app.current_tenant_id = $1`. Tipagem monetária estrita `NUMERIC(14,4)`.
- **Gerenciamento de Estado**: Redis 7 (Máquina de Estados Finita - FSM por tenant e debouncing para aglutinação de mensagens/áudios).
- **Armazenamento de Mídia**: MinIO (S3-compatible) para recibos e comprovantes fiscais.
- **Gateway de Mensagens**: Evolution API (WhatsApp) acoplada a microsserviço em FastAPI (Python 3.11 Async).

## 3. Padrões Críticos de Desenvolvimento
1. **Zero Ponto Flutuante Binário**: Proibido uso de `float` puro para persistência ou cálculos financeiros finais; utilizar sempre estruturas exatas suportadas pelo tipo monetário do PostgreSQL.
2. **Isolamento RLS Obrigatório**: Toda transação de banco de dados executada via pool do `asyncpg` deve iniciar configurando o contexto do tenant correspondente.
3. **Idempotência**: Lançamentos financeiros e webhooks exigem chaves de idempotência unívocas para evitar duplicidade em rajadas de rede móvel.
