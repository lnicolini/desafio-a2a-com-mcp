"""Servidor A2A: Agent Card, SendMessage e GetTask sobre JSON-RPC/HTTP."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .nucleo import NucleoAgente, TarefaInexistente, TarefaTerminal


def montar_card(base_url: str) -> dict:
    return {
        "name": "Central de Salas",
        "description": "Reserva salas de reuniao da Hill Valley Tech.",
        "provider": {"organization": "Hill Valley Tech", "url": "https://hillvalley.example"},
        "version": "1.0.0",
        "supportedInterfaces": [
            {"url": base_url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "reservar-sala",
                "name": "Reservar sala",
                "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
                "tags": ["salas", "agenda"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
                "examples": [
                    "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
                ],
            }
        ],
    }


class Tratador(BaseHTTPRequestHandler):
    nucleo: NucleoAgente | None = None
    card: dict | None = None

    def _responder(self, status: int, payload: dict) -> None:
        dados = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:
        if self.path == "/.well-known/agent-card.json":
            self._responder(200, self.card or {})
        else:
            self._responder(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/a2a":
            self._responder(404, {"error": "not found"})
            return
        tamanho = int(self.headers.get("Content-Length", "0"))
        corpo = json.loads(self.rfile.read(tamanho))
        traceparent = self.headers.get("traceparent")
        metodo = corpo.get("method")
        params = corpo.get("params") or {}
        request_id = corpo.get("id")

        try:
            resultado = self._executar(metodo, params, traceparent)
        except TarefaInexistente:
            resultado = {"error": {"code": -32000, "message": "Task nao encontrada"}}
        except TarefaTerminal as exc:
            resultado = {"error": {"code": -32001, "message": f"Task em estado terminal: {exc.estado}"}}
        except Exception as exc:  # pragma: no cover - nunca deve acontecer
            resultado = {"error": {"code": -32603, "message": str(exc)}}

        resposta: dict = {"jsonrpc": "2.0", "id": request_id}
        if "error" in resultado:
            resposta["error"] = resultado["error"]
        else:
            resposta["result"] = resultado
        self._responder(200, resposta)

    def _executar(self, metodo: str, params: dict, traceparent: str | None) -> dict:
        if metodo == "SendMessage":
            mensagem = params.get("message") or {}
            partes = mensagem.get("parts") or []
            texto = " ".join(p.get("text", "") for p in partes)
            task_id = mensagem.get("taskId")
            tarefa = self.nucleo.receber(texto, task_id, traceparent)
            return {"task": tarefa.para_json()}
        if metodo == "GetTask":
            tarefa = self.nucleo.consultar(params.get("id"))
            if tarefa is None:
                raise TarefaInexistente()
            return {"task": tarefa.para_json()}
        return {"error": {"code": -32601, "message": f"Metodo desconhecido: {metodo}"}}

    def log_message(self, format: str, *args) -> None:  # silencia o log padrão
        pass


def servir(nucleo: NucleoAgente, host: str, porta: int) -> ThreadingHTTPServer:
    Tratador.nucleo = nucleo
    Tratador.card = montar_card(f"http://{host}:{porta}/a2a")
    return ThreadingHTTPServer((host, porta), Tratador)
