"""Concrete dependencies shared by workflow and stage handlers."""

from dataclasses import dataclass

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.runtime.jev import Judge
from experiment_design.runtime.llm import LLMClient


@dataclass
class Deps:
    store: Store
    llm: LLMClient
    judge: Judge
    run: RunConfig
