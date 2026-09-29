"""Ponto de entrada do servidor MCP (``python3 -m central_salas``)."""

from __future__ import annotations

import os

import anyio

from .dominio import CentralDeSalas
from .servidor import construir_servidor


def main() -> None:
    central = CentralDeSalas()
    server = construir_servidor(central)
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "7301"))
    anyio.run(
        lambda: server.run_streamable_http_async(
            host=host,
            port=port,
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )
    )


if __name__ == "__main__":
    main()
