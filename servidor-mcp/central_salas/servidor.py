"""Montagem do servidor MCP: tools, resource, middleware e o ciclo MRTR."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping

from mcp import MCPError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from mcp_types import (
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    ClientCapabilities,
    ElicitationCapability,
    ElicitRequest,
    ElicitRequestFormParams,
    FormElicitationCapability,
    InputRequiredResult,
    MissingRequiredClientCapabilityErrorData,
)

from .contratos import ConflitoDTO, DisponibilidadeDTO, ListaSalasDTO, ReservaDTO, SalaDTO
from .dominio import CentralDeSalas, ErroDeDominio, SEM_ALTERNATIVAS

CHAVE_ELICITACAO = "alternativa_de_sala"
TEXTO_ELICITACAO = "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa."


def _schema_da_escolha(alternativas: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "sala": {
                "type": "string",
                "title": "Sala",
                "description": "Sala alternativa escolhida",
                "enum": alternativas,
            }
        },
        "required": ["sala"],
    }


def _exigir_capability_form(ctx: Context) -> None:
    caps = ctx.client_capabilities
    if caps is not None and caps.elicitation is not None and caps.elicitation.form is not None:
        return
    exigido = ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))
    raise MCPError(
        code=MISSING_REQUIRED_CLIENT_CAPABILITY,
        message=f"Client did not declare the form elicitation capability required by resolver {CHAVE_ELICITACAO!r}",
        data=MissingRequiredClientCapabilityErrorData(
            required_capabilities=exigido
        ).model_dump(by_alias=True, mode="json", exclude_none=True),
    )


class _ObservarRequests:
    """Registra método, id e traceparent de cada request no stderr."""

    async def __call__(self, ctx, call_next):
        meta = ctx.meta if isinstance(ctx.meta, Mapping) else {}
        print(
            f"[mcp] {ctx.method} id={ctx.request_id} traceparent={meta.get('traceparent', '')}",
            file=sys.stderr,
            flush=True,
        )
        return await call_next(ctx)


def construir_servidor(central: CentralDeSalas) -> MCPServer:
    server = MCPServer(
        name="central-de-salas",
        version="1.0.0",
        request_state_security=RequestStateSecurity(keys=[os.environ["REQUEST_STATE_SECRET"]], ttl=600.0),
        middleware=[_ObservarRequests()],
    )

    @server.tool()
    def listar_salas() -> ListaSalasDTO:
        """Lista todas as salas com capacidade e recursos."""
        return ListaSalasDTO(
            salas=[
                SalaDTO(id=s.id, nome=s.nome, capacidade=s.capacidade, recursos=s.recursos)
                for s in central.todas_as_salas()
            ]
        )

    @server.tool()
    def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> DisponibilidadeDTO:
        """Diz se uma sala esta livre no intervalo, e quais reservas conflitam."""
        try:
            central.validar(sala, inicio, fim)
        except ErroDeDominio as exc:
            raise ToolError(str(exc)) from exc
        conflitos = central.conflitos(sala, inicio, fim)
        return DisponibilidadeDTO(
            sala=sala,
            livre=not conflitos,
            conflitos=[
                ConflitoDTO(id=c.id, inicio=c.inicio, fim=c.fim, responsavel=c.responsavel)
                for c in conflitos
            ],
        )

    @server.tool()
    def reservar_sala(
        sala: str,
        inicio: str,
        fim: str,
        responsavel: str,
        ctx: Context,
    ) -> ReservaDTO | InputRequiredResult:
        """Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar."""
        # Retry: o requestState (verificado pelo middleware) reconstrói o pedido
        # original. Os argumentos reenviados não são confiáveis.
        if ctx.request_state is not None:
            pedido = json.loads(ctx.request_state)
            resposta = (ctx.input_responses or {}).get(CHAVE_ELICITACAO)
            if resposta is None or getattr(resposta, "action", None) in ("decline", "cancel"):
                return ReservaDTO(reservado=False, motivo="recusado")
            escolhida = (getattr(resposta, "content", None) or {}).get("sala")
            return ReservaDTO(**central.efetivar(escolhida, pedido["inicio"], pedido["fim"], pedido["responsavel"]))

        try:
            central.validar(sala, inicio, fim)
        except ErroDeDominio as exc:
            raise ToolError(str(exc)) from exc

        if central.esta_livre(sala, inicio, fim):
            return ReservaDTO(**central.efetivar(sala, inicio, fim, responsavel))

        alternativas = central.sugerir_alternativas(sala, inicio, fim)
        if not alternativas:
            raise ToolError(SEM_ALTERNATIVAS)

        _exigir_capability_form(ctx)

        pedido_selado = json.dumps(
            {"sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel},
            sort_keys=True,
        )
        return InputRequiredResult(
            input_requests={
                CHAVE_ELICITACAO: ElicitRequest(
                    params=ElicitRequestFormParams(
                        message=TEXTO_ELICITACAO,
                        requested_schema=_schema_da_escolha(alternativas),
                    )
                )
            },
            request_state=pedido_selado,
        )

    @server.resource("politica://uso", name="Politica de uso", mime_type="text/markdown")
    def politica() -> str:
        """Texto da politica de uso das salas."""
        return central.texto_politica

    return server
