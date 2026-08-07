"""Prospective, append-only model evaluation infrastructure."""

from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.registry import ForwardRegistry

__all__ = ["ForwardRegistry", "RegistryDatabase"]
