"""
TCP Stratum API endpoints
"""

from fastapi import APIRouter, Depends
from app.dependencies import get_tcp_stratum_server

router = APIRouter(prefix="/tcp-stratum", tags=["tcp-stratum"])


@router.get("/stats")
async def get_tcp_stratum_stats(
        tcp_server=Depends(get_tcp_stratum_server)
):
    """Статистика TCP Stratum сервера"""
    return {
        "status": "running",
        "host": tcp_server.host,
        "port": tcp_server.port,
        "active_connections": len(tcp_server.connections),
        "active_miners": len(tcp_server.miners),
        "protocol": "stratum+tcp"
    }


@router.get("/connections")
async def get_tcp_connections(
        tcp_server=Depends(get_tcp_stratum_server)
):
    """Список активных TCP подключений"""
    connections = []
    for client_id, writer in tcp_server.connections.items():
        miner_address = tcp_server.miners.get(client_id, "unknown")
        connections.append({
            "client_id": client_id,
            "miner_address": miner_address,
            "remote_address": writer.get_extra_info('peername')
        })

    return {
        "total": len(connections),
        "connections": connections
    }