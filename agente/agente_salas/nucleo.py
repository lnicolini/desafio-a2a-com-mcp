"""Núcleo do agente: interpretação determinística, máquina de estados e a ponte.

Aqui acontece a costura entre os dois protocolos sem sessão. Quando o servidor
MCP responde ``input_required``, o agente guarda o ``requestState`` opaco (e a
chave da elicitação) ligado àquela Task, pausa em ``TASK_STATE_INPUT_REQUIRED``
e, na continuação, repete o ``tools/call`` com um id novo, ecoando
``inputResponses`` e o ``requestState`` sem abri-lo.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field

from . import mcp_cliente


class TarefaInexistente(Exception):
    pass


class TarefaTerminal(Exception):
    def __init__(self, estado: str) -> None:
        super().__init__(estado)
        self.estado = estado


@dataclass
class Tarefa:
    id: str
    context_id: str
    estado: str = "TASK_STATE_SUBMITTED"
    mensagens: list[dict] = field(default_factory=list)
    artifact: dict | None = None
    # estado da ponte — privado, nunca sai no JSON da Task
    request_state: str | None = None
    chave_elicitation: str | None = None
    alternativas: list[str] = field(default_factory=list)
    pedido: dict | None = None

    def para_json(self) -> dict:
        status: dict = {"state": self.estado}
        if self.mensagens:
            status["message"] = self.mensagens[-1]
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": status,
            "history": list(self.mensagens),
            "artifacts": [self.artifact] if self.artifact else [],
        }


def interpretar_reserva(texto: str) -> dict | None:
    if not texto.startswith("reservar"):
        return None
    pares: dict[str, str] = {}
    for token in texto.split()[1:]:
        if "=" in token:
            chave, _, valor = token.partition("=")
            pares[chave] = valor
    if not {"sala", "inicio", "fim", "responsavel"} <= set(pares):
        return None
    return pares


def interpretar_escolha(texto: str) -> str | None:
    if texto.startswith("escolha="):
        return texto[len("escolha="):]
    return None


def extrair_versao(politica: str) -> str | None:
    cabecalho = politica.splitlines()[0]
    return cabecalho.split(":", 1)[1].strip() if ":" in cabecalho else None


def linha_alternativas(ids: list[str]) -> str:
    return "alternativas: " + ", ".join(ids)


def extrair_enum(pedido: dict) -> list[str]:
    schema = (pedido.get("params") or {}).get("requestedSchema") or {}
    campo = (schema.get("properties") or {}).get("sala") or {}
    if campo.get("enum"):
        return list(campo["enum"])
    if "const" in campo:
        return [campo["const"]]
    return []


class NucleoAgente:
    """Guarda as Tasks e costura a ponte entre MCP e A2A."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tarefas: dict[str, Tarefa] = {}
        self._versao_politica: str | None = None

    def iniciar(self) -> None:
        mcp_cliente.listar_ferramentas()
        self._versao_politica = extrair_versao(mcp_cliente.ler_politica())

    # --- criação e acesso às Tasks ---------------------------------------

    def _nova(self) -> Tarefa:
        tarefa = Tarefa(id="task-" + uuid.uuid4().hex[:12], context_id="ctx-" + uuid.uuid4().hex[:12])
        with self._lock:
            self._tarefas[tarefa.id] = tarefa
        return tarefa

    def _buscar(self, task_id: str) -> Tarefa | None:
        with self._lock:
            return self._tarefas.get(task_id)

    def _anexar(self, tarefa: Tarefa, estado: str, texto: str) -> None:
        mensagem = {
            "messageId": "msg-" + uuid.uuid4().hex[:12],
            "role": "ROLE_AGENT",
            "parts": [{"text": texto}],
            "taskId": tarefa.id,
            "contextId": tarefa.context_id,
        }
        tarefa.estado = estado
        tarefa.mensagens.append(mensagem)

    @staticmethod
    def _limpar_ponte(tarefa: Tarefa) -> None:
        tarefa.request_state = None
        tarefa.chave_elicitation = None
        tarefa.alternativas = []
        tarefa.pedido = None

    # --- SendMessage ------------------------------------------------------

    def receber(self, texto: str, task_id: str | None, traceparent: str | None) -> Tarefa:
        traceparent_mcp = mcp_cliente.propagar_traceparent(traceparent)
        if task_id is None:
            return self._novo_pedido(texto, traceparent_mcp)
        return self._continuar(task_id, texto, traceparent_mcp)

    def _novo_pedido(self, texto: str, traceparent: str | None) -> Tarefa:
        pares = interpretar_reserva(texto)
        if pares is None:
            tarefa = self._nova()
            self._anexar(tarefa, "TASK_STATE_FAILED", "Pedido em formato invalido")
            return tarefa

        tarefa = self._nova()
        self._anexar(tarefa, "TASK_STATE_WORKING", "reservando")
        pedido = {
            "sala": pares["sala"],
            "inicio": pares["inicio"],
            "fim": pares["fim"],
            "responsavel": pares["responsavel"],
        }
        tarefa.pedido = pedido
        resposta = mcp_cliente.reservar(pedido, traceparent=traceparent)
        return self._processar(tarefa, resposta)

    def _continuar(self, task_id: str, texto: str, traceparent: str | None) -> Tarefa:
        tarefa = self._buscar(task_id)
        if tarefa is None:
            raise TarefaInexistente()
        if tarefa.estado in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
            raise TarefaTerminal(tarefa.estado)
        if tarefa.estado != "TASK_STATE_INPUT_REQUIRED":
            return tarefa

        escolha = interpretar_escolha(texto)
        if escolha is None:
            return tarefa

        if escolha == "recusar":
            mcp_cliente.reservar(
                tarefa.pedido,
                traceparent=traceparent,
                input_responses={tarefa.chave_elicitation: {"action": "decline"}},
                request_state=tarefa.request_state,
            )
            self._limpar_ponte(tarefa)
            self._anexar(tarefa, "TASK_STATE_CANCELED", "Reserva recusada.")
            return tarefa

        if escolha not in tarefa.alternativas:
            self._anexar(tarefa, "TASK_STATE_INPUT_REQUIRED", linha_alternativas(tarefa.alternativas))
            return tarefa

        resposta = mcp_cliente.reservar(
            tarefa.pedido,
            traceparent=traceparent,
            input_responses={tarefa.chave_elicitation: {"action": "accept", "content": {"sala": escolha}}},
            request_state=tarefa.request_state,
        )
        self._limpar_ponte(tarefa)
        return self._processar(tarefa, resposta)

    # --- processamento da resposta MCP -----------------------------------

    def _processar(self, tarefa: Tarefa, resposta: dict) -> Tarefa:
        if "error" in resposta:
            self._anexar(tarefa, "TASK_STATE_FAILED", resposta["error"].get("message", "erro"))
            return tarefa

        resultado = resposta.get("result") or {}

        if resultado.get("resultType") == "input_required":
            pedidos = resultado.get("inputRequests") or {}
            chave = next(iter(pedidos), None)
            alternativas = extrair_enum(pedidos.get(chave, {})) if chave else []
            tarefa.chave_elicitation = chave
            tarefa.alternativas = alternativas
            tarefa.request_state = resultado.get("requestState")
            self._anexar(tarefa, "TASK_STATE_INPUT_REQUIRED", linha_alternativas(alternativas))
            return tarefa

        if resultado.get("isError"):
            texto = " ".join(p.get("text", "") for p in resultado.get("content", []))
            self._anexar(tarefa, "TASK_STATE_FAILED", texto)
            return tarefa

        estruturado = resultado.get("structuredContent") or {}
        tarefa.artifact = self._montar_artifact(estruturado)
        self._anexar(
            tarefa,
            "TASK_STATE_COMPLETED",
            f"Reserva {estruturado.get('reserva')} confirmada na {estruturado.get('sala')}.",
        )
        return tarefa

    def _montar_artifact(self, estruturado: dict) -> dict:
        return {
            "artifactId": "art-" + uuid.uuid4().hex[:12],
            "name": "reserva",
            "parts": [
                {
                    "text": json.dumps(
                        {
                            "reserva": estruturado.get("reserva"),
                            "sala": estruturado.get("sala"),
                            "inicio": estruturado.get("inicio"),
                            "fim": estruturado.get("fim"),
                            "responsavel": estruturado.get("responsavel"),
                            "politica": self._versao_politica,
                        },
                        ensure_ascii=False,
                    )
                }
            ],
        }

    # --- GetTask ----------------------------------------------------------

    def consultar(self, task_id: str) -> Tarefa | None:
        return self._buscar(task_id)
