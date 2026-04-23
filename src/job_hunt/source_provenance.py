from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


SourceAuthority = Literal["system_of_record", "derived"]
SourcePrecedence = Literal["ats_public", "government_api", "board_search", "aggregator"]


@dataclass(frozen=True)
class DiscoverySourceDefinition:
    provider: str
    runtime_source: str
    discovered_via_source: str
    authority: SourceAuthority
    precedence: SourcePrecedence


SOURCE_DEFINITIONS: Final[dict[str, DiscoverySourceDefinition]] = {
    "greenhouse": DiscoverySourceDefinition(
        provider="greenhouse",
        runtime_source="greenhouse",
        discovered_via_source="greenhouse_board",
        authority="system_of_record",
        precedence="ats_public",
    ),
    "lever": DiscoverySourceDefinition(
        provider="lever",
        runtime_source="lever",
        discovered_via_source="lever_board",
        authority="system_of_record",
        precedence="ats_public",
    ),
    "careers": DiscoverySourceDefinition(
        provider="careers",
        runtime_source="careers_html",
        discovered_via_source="careers_html",
        authority="derived",
        precedence="board_search",
    ),
    "indeed_search": DiscoverySourceDefinition(
        provider="indeed_search",
        runtime_source="indeed_search",
        discovered_via_source="indeed_search",
        authority="derived",
        precedence="board_search",
    ),
    "ashby": DiscoverySourceDefinition(
        provider="ashby",
        runtime_source="ashby",
        discovered_via_source="ashby_public_api",
        authority="system_of_record",
        precedence="ats_public",
    ),
    "workable": DiscoverySourceDefinition(
        provider="workable",
        runtime_source="workable",
        discovered_via_source="workable_public_api",
        authority="system_of_record",
        precedence="ats_public",
    ),
    "usajobs": DiscoverySourceDefinition(
        provider="usajobs",
        runtime_source="usajobs",
        discovered_via_source="usajobs_api",
        authority="system_of_record",
        precedence="government_api",
    ),
}

SOURCE_NAME_MAP: Final[dict[str, tuple[str, str]]] = {
    provider: (definition.runtime_source, definition.discovered_via_source)
    for provider, definition in SOURCE_DEFINITIONS.items()
}
DISCOVERY_SOURCE_TOKENS: Final[tuple[str, ...]] = tuple(SOURCE_DEFINITIONS.keys())

DISCOVERED_VIA_TO_PROVIDER: Final[dict[str, str]] = {
    definition.discovered_via_source: provider
    for provider, definition in SOURCE_DEFINITIONS.items()
}
DISCOVERED_VIA_TO_PROVIDER["careers_html_review"] = "careers"

RUNTIME_SOURCE_TO_PROVIDER: Final[dict[str, str]] = {
    definition.runtime_source: provider
    for provider, definition in SOURCE_DEFINITIONS.items()
}

SOURCE_PRECEDENCE_ORDER: Final[dict[SourcePrecedence, int]] = {
    "aggregator": 0,
    "board_search": 1,
    "government_api": 2,
    "ats_public": 3,
}


def source_definition(provider: str) -> DiscoverySourceDefinition:
    return SOURCE_DEFINITIONS[provider]


def provider_for_discovered_via_source(value: str) -> str | None:
    return DISCOVERED_VIA_TO_PROVIDER.get(value)


def provider_for_runtime_source(value: str) -> str | None:
    return RUNTIME_SOURCE_TO_PROVIDER.get(value)


def compare_source_precedence(left: SourcePrecedence, right: SourcePrecedence) -> int:
    left_rank = SOURCE_PRECEDENCE_ORDER[left]
    right_rank = SOURCE_PRECEDENCE_ORDER[right]
    if left_rank < right_rank:
        return -1
    if left_rank > right_rank:
        return 1
    return 0


def primary_source_record(provider: str) -> dict[str, str]:
    definition = source_definition(provider)
    return {
        "provider": definition.provider,
        "authority": definition.authority,
        "precedence": definition.precedence,
    }


def observed_source_record(
    provider: str,
    company: str,
    *,
    observed_at: str,
    listing_updated_at: str | None = None,
    confidence: str = "high",
) -> dict[str, str | None]:
    definition = source_definition(provider)
    return {
        "provider": definition.provider,
        "authority": definition.authority,
        "precedence": definition.precedence,
        "company": company,
        "observed_at": observed_at,
        "listing_updated_at": listing_updated_at or None,
        "confidence": confidence,
    }


def _seed_legacy_provider(lead: dict) -> tuple[str | None, dict | None]:
    primary = lead.get("primary_source")
    if isinstance(primary, dict):
        provider = primary.get("provider")
        if isinstance(provider, str) and provider in SOURCE_DEFINITIONS:
            return provider, primary

    discovered_via = lead.get("discovered_via")
    if isinstance(discovered_via, list):
        for item in discovered_via:
            if not isinstance(item, dict):
                continue
            provider = provider_for_discovered_via_source(str(item.get("source") or ""))
            if provider is None:
                continue
            return provider, primary_source_record(provider)

    provider = provider_for_runtime_source(str(lead.get("source") or ""))
    if provider is not None:
        return provider, primary_source_record(provider)
    return None, None


def append_discovery_observation(
    lead: dict,
    provider: str,
    company: str,
    *,
    observed_at: str,
    listing_updated_at: str | None = None,
    confidence: str = "high",
    discovered_via_source_override: str | None = None,
) -> dict:
    current_provider, current_primary = _seed_legacy_provider(lead)
    if current_provider is not None and not isinstance(lead.get("primary_source"), dict):
        lead["primary_source"] = current_primary

    observed_sources = lead.get("observed_sources")
    if not isinstance(observed_sources, list):
        observed_sources = []
        if current_provider is not None:
            seed_timestamp = str(lead.get("ingested_at") or observed_at)
            seed_company = str(lead.get("company") or company)
            seed_confidence = "high"
            seed_updated_at = None
            discovered_via = lead.get("discovered_via")
            if isinstance(discovered_via, list):
                for item in discovered_via:
                    if not isinstance(item, dict):
                        continue
                    if provider_for_discovered_via_source(str(item.get("source") or "")) != current_provider:
                        continue
                    seed_company = str(item.get("company") or seed_company)
                    seed_timestamp = str(item.get("discovered_at") or seed_timestamp)
                    seed_updated_at = item.get("listing_updated_at")
                    seed_confidence = str(item.get("confidence") or seed_confidence)
                    break
            observed_sources.append(
                observed_source_record(
                    current_provider,
                    seed_company,
                    observed_at=seed_timestamp,
                    listing_updated_at=str(seed_updated_at or "") or None,
                    confidence=seed_confidence,
                )
            )

    observed_sources.append(
        observed_source_record(
            provider,
            company,
            observed_at=observed_at,
            listing_updated_at=listing_updated_at,
            confidence=confidence,
        )
    )
    lead["observed_sources"] = observed_sources

    discovered_via = lead.get("discovered_via")
    if not isinstance(discovered_via, list):
        discovered_via = []
    definition = source_definition(provider)
    discovered_via.append({
        "source": discovered_via_source_override or definition.discovered_via_source,
        "company": company,
        "discovered_at": observed_at,
        "listing_updated_at": listing_updated_at or None,
        "confidence": confidence,
    })
    lead["discovered_via"] = discovered_via

    if current_provider is None:
        winner = provider
    else:
        current_definition = source_definition(current_provider)
        incoming_definition = source_definition(provider)
        if compare_source_precedence(
            incoming_definition.precedence,
            current_definition.precedence,
        ) > 0:
            winner = provider
        else:
            winner = current_provider
    lead["primary_source"] = primary_source_record(winner)
    lead["source"] = source_definition(winner).runtime_source
    return lead
