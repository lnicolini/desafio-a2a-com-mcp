"""Domínio da central de salas: dados em memória e regras de uso.

Este módulo não conhece MCP nem A2A. Ele só carrega ``dados/`` no início do
processo, mantém o estado das reservas e aplica as três regras da política de
uso. As violações de negócio saem como :class:`ErroDeDominio`; transformar isso
em ``isError`` é responsabilidade da camada de tool (``servidor.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_DIRETORIO_DADOS = Path(__file__).resolve().parents[2] / "dados"

# Janela de uso (horário de São Paulo, -03:00) e limite de duração.
ABERTURA = datetime.strptime("08:00", "%H:%M").time()
FECHAMENTO = datetime.strptime("20:00", "%H:%M").time()
DURACAO_MAXIMA = timedelta(hours=2)

# Texto exato exigido pelo enunciado — fonte única de verdade.
SALA_INEXISTENTE = "Sala inexistente: {}"
FORA_DA_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
DURACAO_EXCEDIDA = "Duracao acima do limite: a politica permite no maximo 2 horas"
INTERVALO_INVALIDO = "Intervalo invalido: fim deve ser posterior a inicio"
SEM_ALTERNATIVAS = "Sem alternativas disponiveis no intervalo"


@dataclass(frozen=True)
class Sala:
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


@dataclass(frozen=True)
class Reserva:
    id: str
    sala: str
    inicio: str
    fim: str
    responsavel: str


class ErroDeDominio(Exception):
    """Uma regra de negócio foi violada; a mensagem é o texto exato do contrato."""


def _instante(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _colidem(a_inicio: datetime, a_fim: datetime, b_inicio: datetime, b_fim: datetime) -> bool:
    return a_inicio < b_fim and b_inicio < a_fim


class CentralDeSalas:
    """Estado das salas e reservas, com a lógica de disponibilidade e reserva."""

    def __init__(self) -> None:
        self._salas = [Sala(**item) for item in json.loads((_DIRETORIO_DADOS / "salas.json").read_text())]
        self._reservas = [Reserva(**item) for item in json.loads((_DIRETORIO_DADOS / "reservas.json").read_text())]
        politica = (_DIRETORIO_DADOS / "politica-de-uso.md").read_text()
        self.texto_politica = politica
        self.versao_politica = politica.splitlines()[0].split(":", 1)[1].strip()

    def todas_as_salas(self) -> list[Sala]:
        return list(self._salas)

    def buscar_sala(self, id_sala: str) -> Sala | None:
        return next((s for s in self._salas if s.id == id_sala), None)

    def conflitos(self, id_sala: str, inicio: str, fim: str) -> list[Reserva]:
        di, df = _instante(inicio), _instante(fim)
        return [
            r
            for r in self._reservas
            if r.sala == id_sala and _colidem(di, df, _instante(r.inicio), _instante(r.fim))
        ]

    def esta_livre(self, id_sala: str, inicio: str, fim: str) -> bool:
        return not self.conflitos(id_sala, inicio, fim)

    def validar(self, id_sala: str, inicio: str, fim: str) -> None:
        if self.buscar_sala(id_sala) is None:
            raise ErroDeDominio(SALA_INEXISTENTE.format(id_sala))
        di, df = _instante(inicio), _instante(fim)
        if not (ABERTURA <= di.time() and df.time() <= FECHAMENTO):
            raise ErroDeDominio(FORA_DA_JANELA)
        if df <= di:
            raise ErroDeDominio(INTERVALO_INVALIDO)
        if df - di > DURACAO_MAXIMA:
            raise ErroDeDominio(DURACAO_EXCEDIDA)

    def sugerir_alternativas(self, id_sala: str, inicio: str, fim: str) -> list[str]:
        """Salas livres no intervalo com capacidade >= a da pedida, no máx. 3.

        Ordenadas por capacidade crescente e, em empate, por id alfabético.
        """
        minimo = self.buscar_sala(id_sala).capacidade
        candidatas = [s for s in self._salas if s.capacidade >= minimo and self.esta_livre(s.id, inicio, fim)]
        candidatas.sort(key=lambda s: (s.capacidade, s.id))
        return [s.id for s in candidatas[:3]]

    def efetivar(self, id_sala: str, inicio: str, fim: str, responsavel: str) -> dict:
        reserva_id = f"res-{len(self._reservas) + 1:04d}"
        self._reservas.append(Reserva(reserva_id, id_sala, inicio, fim, responsavel))
        return {
            "reserva": reserva_id,
            "reservado": True,
            "sala": id_sala,
            "inicio": inicio,
            "fim": fim,
            "responsavel": responsavel,
            "politica": self.versao_politica,
            "motivo": None,
        }
