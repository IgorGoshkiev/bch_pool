"""TCP Stratum API endpoints"""
from datetime import datetime, UTC

from fastapi import APIRouter, HTTPException, status
from app.schemas.models import ApiResponse
from app.dependencies import tcp_stratum_server

router = APIRouter(prefix="/tcp-stratum", tags=["tcp-stratum"])


@router.get("/stats", response_model=ApiResponse)
async def get_tcp_stratum_stats():
    """Статистика TCP Stratum сервера"""
    try:
        return ApiResponse(
            status="success",
            message="Статистика TCP Stratum сервера получена",
            data={
                "status": "running",
                "host": tcp_stratum_server.host,
                "port": tcp_stratum_server.port,
                "active_connections": len(tcp_stratum_server.connections),
                "active_miners": len(tcp_stratum_server.miners),
                "protocol": "stratum+tcp",
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения статистики TCP сервера: {str(e)}"
        )


@router.get("/connections", response_model=ApiResponse)
async def get_tcp_connections():
    """Список активных TCP подключений"""
    try:
        connections = []
        for client_id, writer in tcp_stratum_server.connections.items():
            miner_address = tcp_stratum_server.miners.get(client_id, "unknown")

            # Получаем информацию о подключении
            try:
                peername = writer.get_extra_info('peername')
                remote_address = f"{peername[0]}:{peername[1]}" if peername else "unknown"
            except Exception as e:

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Ошибка получения remote_address: {str(e)}"
                )

            connections.append({
                "client_id": client_id,
                "miner_address": miner_address,
                "remote_address": remote_address,
                "connection_time": datetime.now(UTC).isoformat()
            })

        return ApiResponse(
            status="success",
            message="Список TCP подключений получен",
            data={
                "total": len(connections),
                "connections": connections,
                "timestamp": datetime.now(UTC).isoformat()
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка получения списка подключений: {str(e)}"
        )