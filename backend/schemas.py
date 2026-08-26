from decimal import Decimal
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


# =============================================================================
# MODELAGEM DO PAYLOAD DA EVOLUTION API (WHATSAPP)
# =============================================================================

class EvolutionKey(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    remote_jid: str = Field(alias="remoteJid", description="Identificador JID do contato/grupo")
    from_me: bool = Field(alias="fromMe", description="Indica se a mensagem foi enviada pelo bot")
    id: str = Field(description="ID único da mensagem gerado pelo WhatsApp")


class EvolutionMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    conversation: Optional[str] = Field(None, description="Conteúdo textual de mensagens simples")
    extended_text_message: Optional[Dict[str, Any]] = Field(
        None, alias="extendedTextMessage", description="Conteúdo de mensagens com formatação ou links"
    )
    image_message: Optional[Dict[str, Any]] = Field(
        None, alias="imageMessage", description="Metadados de mensagens de imagem"
    )


class EvolutionData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    key: EvolutionKey
    message: EvolutionMessage
    message_type: str = Field(alias="messageType", description="Tipo da mensagem (ex: extendedTextMessage)")


class WebhookPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    event: str = Field(description="Tipo de evento disparado pela Evolution API")
    data: EvolutionData


# =============================================================================
# SUB-MODELOS DE TELEMETRIA DE COMBUSTÍVEL (ESTOQUE FINANCEIRO)
# =============================================================================

class FuelMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    tipo_veiculo: str
    is_flex: bool
    is_hibrido: bool
    is_eletrico: bool
    capacidade_tanque_l: Decimal = Field(description="Capacidade física máxima de combustível líquido")
    capacidade_bateria_kwh: Decimal = Field(description="Capacidade máxima de armazenamento elétrico")
    qtd_tanques: int


class LiquidFuelStock(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    litros: Decimal = Field(description="Volume total de combustível líquido no tanque")
    custo_total: Decimal = Field(description="Custo acumulado do estoque líquido (Bank-Grade precision)")
    gasolina_litros: Decimal
    etanol_litros: Decimal
    gasolina_proporcao: Decimal = Field(description="Percentual químico de gasolina na mistura (0.0 a 1.0)")
    etanol_proporcao: Decimal = Field(description="Percentual químico de etanol na mistura (0.0 a 1.0)")
    km_l_gasolina: Decimal
    km_l_etanol: Decimal


class ElectricStock(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    kwh: Decimal = Field(description="Carga atual disponível em kWh")
    custo_total: Decimal = Field(description="Custo total acumulado das recargas")
    km_kwh: Decimal = Field(description="Eficiência energética (km por kWh)")


class GNVStock(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    m3: Decimal = Field(description="Volume atual de GNV em metros cúbicos")
    custo_total: Decimal
    km_m3: Decimal = Field(description="Eficiência média em metros cúbicos")


# =============================================================================
# MODELO CONSOLIDADO DE VEÍCULO E TELEMETRIA
# =============================================================================

class EstoqueFinanceiro(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    meta: FuelMeta
    liquido: LiquidFuelStock
    eletricidade: ElectricStock
    gnv: GNVStock


class VeiculoSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    id: UUID = Field(description="Identificador único global do veículo (UUID4)")
    placa: str
    modelo: str
    tipo_combustivel: str
    estoque_financeiro: EstoqueFinanceiro
    locadora: Optional[str] = Field(None, description="Nome da locadora ou 'Proprietário'")
    custo_aluguel_semanal: Decimal = Field(description="Custo fixo semanal para rateio do DRE")
    franquia_km_semanal: Decimal = Field(description="Limite de rodagem sem custo excedente")
    valor_km_excedente: Decimal = Field(description="Custo variável por quilômetro extra rodado")
    escala_trabalho: str
    contrato_personalizado: bool
