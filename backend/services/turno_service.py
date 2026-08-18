from services.database_service import DatabaseService

class TurnoService:
    @staticmethod
    async def abrir_turno(driver_id: str, km_inicial: float):
        async with DatabaseService.get_connection() as conn:
            async with conn.transaction():
                # Injeção isolada de escopo RLS
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true)", driver_id)
                
                ativo = await conn.fetchval(
                    "SELECT id FROM turnos WHERE driver_id = $1 AND status = 'ABERTO'", driver_id
                )
                if ativo:
                    return {"status": "error", "message": "Já existe um turno aberto para este motorista."}

                query = """
                    INSERT INTO turnos (driver_id, km_inicial, status, data_inicio)
                    VALUES ($1, $2, 'ABERTO', NOW())
                    RETURNING id, data_inicio;
                """
                row = await conn.fetchrow(query, driver_id, km_inicial)
                return {"status": "success", "turno_id": str(row["id"])}

    @staticmethod
    async def fechar_turno(driver_id: str, km_final: float):
        async with DatabaseService.get_connection() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_driver_id', $1, true)", driver_id)
                
                query = """
                    UPDATE turnos 
                    SET km_final = $2, status = 'FECHADO', data_fim = NOW()
                    WHERE driver_id = $1 AND status = 'ABERTO'
                    RETURNING id, km_inicial, km_final, data_inicio, data_fim;
                """
                row = await conn.fetchrow(query, driver_id, km_final)
                if not row:
                    return {"status": "error", "message": "Nenhum turno aberto encontrado para fechamento."}
                
                km_rodados = row["km_final"] - row["km_inicial"]
                return {"status": "success", "turno_id": str(row["id"]), "km_rodados": km_rodados}
