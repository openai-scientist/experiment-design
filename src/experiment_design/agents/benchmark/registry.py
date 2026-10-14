"""Curated resources of the loaded pack; a domain never sees another domain's entries."""

from experiment_design.packs import Pack, RegistryEntry


def registry_entries(pack: Pack) -> tuple[RegistryEntry, ...]:
    return pack.registry
