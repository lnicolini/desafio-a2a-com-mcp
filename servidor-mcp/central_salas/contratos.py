"""Modelos de saída das tools (geram structuredContent + outputSchema)."""

from __future__ import annotations

from pydantic import BaseModel


class SalaDTO(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaSalasDTO(BaseModel):
    salas: list[SalaDTO]


class ConflitoDTO(BaseModel):
    id: str
    inicio: str
    fim: str
    responsavel: str


class DisponibilidadeDTO(BaseModel):
    sala: str
    livre: bool
    conflitos: list[ConflitoDTO]


class ReservaDTO(BaseModel):
    reserva: str | None = None
    reservado: bool = True
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None
