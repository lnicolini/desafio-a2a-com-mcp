"""Ponto de entrada do agente (``python3 -m agente_salas``)."""

from __future__ import annotations

import os

from .a2a_servidor import servir
from .nucleo import NucleoAgente


def main() -> None:
    host = os.environ.get("A2A_HOST", "127.0.0.1")
    porta = int(os.environ.get("A2A_PORT", "7300"))

    nucleo = NucleoAgente()
    nucleo.iniciar()

    server = servir(nucleo, host, porta)
    print(f"agente A2A em http://{host}:{porta}/a2a", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
