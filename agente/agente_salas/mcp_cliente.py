"""Cliente MCP falado na mão (biblioteca padrão).

Controla exatamente o ``_meta`` (protocolVersion, clientInfo, clientCapabilities,
traceparent) e os headers que o transporte espelha do corpo. O agente declara a
elicitação em *form mode* — não a elicitação em geral.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")

PROTOCOLO = "2026-07-28"
CLIENT_INFO = {"name": "agente-central-de-salas", "version": "1.0.0"}
CAPABILIDADES = {"elicitation": {"form": {}}}


def requisitar(method: str, params: dict, *, name: str | None = None, traceparent: str | None = None) -> dict:
    meta: dict = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOLO,
        "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
        "io.modelcontextprotocol/clientCapabilities": CAPABILIDADES,
    }
    if traceparent:
        meta["traceparent"] = traceparent
    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOLO,
        "Mcp-Method": method,
    }
    if name:
        cabecalhos["Mcp-Name"] = name
    corpo = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
        "params": {**params, "_meta": meta},
    }
    req = urllib.request.Request(MCP_URL, data=json.dumps(corpo).encode(), headers=cabecalhos, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode())


def listar_ferramentas(traceparent: str | None = None) -> list[dict]:
    return requisitar("tools/list", {}, traceparent=traceparent).get("result", {}).get("tools", [])


def ler_politica(traceparent: str | None = None) -> str:
    resultado = requisitar("resources/read", {"uri": "politica://uso"}, name="politica://uso", traceparent=traceparent)
    contents = (resultado.get("result") or {}).get("contents") or [{}]
    return contents[0].get("text", "")


def reservar(
    argumentos: dict,
    *,
    traceparent: str | None = None,
    input_responses: dict | None = None,
    request_state: str | None = None,
) -> dict:
    params: dict = {"name": "reservar_sala", "arguments": argumentos}
    if input_responses is not None:
        params["inputResponses"] = input_responses
    if request_state is not None:
        params["requestState"] = request_state
    return requisitar("tools/call", params, name="reservar_sala", traceparent=traceparent)


def propagar_traceparent(entrada: str | None) -> str | None:
    """Mantém o trace-id e troca o span-id a cada salto do lado do agente."""
    if not entrada:
        return None
    partes = entrada.split("-")
    if len(partes) == 4 and partes[0] == "00":
        return f"00-{partes[1]}-{uuid.uuid4().hex[:16]}-{partes[3]}"
    return entrada
