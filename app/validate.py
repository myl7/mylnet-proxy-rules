"""Validate rules before they reach the rendered subscription.

A rule that mihomo cannot parse breaks the whole config for every client, so the
checks here are deliberately strict. Anything not understood is rejected rather
than passed through.

Two families of rule types are left out on purpose:

``MATCH``
    It matches everything, so it would shadow every rule after it, including the
    ``GEOSITE,cn,cn`` and ``MATCH,default`` that the template appends.

``AND`` / ``OR`` / ``NOT`` / ``SUB-RULE``
    Their payloads nest parentheses and commas, which the comma split in
    :mod:`app.rules` cannot represent.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.config import Settings
from app.rules import Rule

NO_RESOLVE = "no-resolve"

_DOMAIN_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})*$")
_KEYWORD_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GEO_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PROCESS_RE = re.compile(r"^[^\s,]+$")
_PORT_RE = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")

_MAX_DOMAIN_LENGTH = 253
_MAX_ASN = 4294967295
_MAX_PORT = 65535

_EXCLUDED_TYPES = {
    "MATCH": "MATCH matches everything and would shadow every rule after it",
    "AND": "logic rules nest commas, which this editor cannot represent",
    "OR": "logic rules nest commas, which this editor cannot represent",
    "NOT": "logic rules nest commas, which this editor cannot represent",
    "SUB-RULE": "sub rules nest commas, which this editor cannot represent",
}


@dataclass(frozen=True, slots=True)
class RuleTypeSpec:
    """One supported rule type, along with what the UI needs to render it."""

    name: str
    placeholder: str
    hint: str
    allows_no_resolve: bool
    validate_payload: Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class ValidationError:
    """One problem, tied to a rule index when it belongs to a single rule."""

    message: str
    index: int | None = None


def _validate_domain(payload: str) -> str | None:
    if len(payload) > _MAX_DOMAIN_LENGTH:
        return f"domain is longer than {_MAX_DOMAIN_LENGTH} characters"
    if "*" in payload or payload.startswith("+"):
        return "wildcards are not allowed here, use DOMAIN-KEYWORD or DOMAIN-REGEX"
    if not _DOMAIN_RE.match(payload):
        return "not a valid domain"
    return None


def _validate_keyword(payload: str) -> str | None:
    if not _KEYWORD_RE.match(payload):
        return "a keyword may only contain letters, digits, dot, underscore and hyphen"
    return None


def _validate_regex(payload: str) -> str | None:
    try:
        re.compile(payload)
    except re.error as error:
        return f"not a valid regular expression: {error}"
    return None


def _validate_geo_name(payload: str) -> str | None:
    if not _GEO_NAME_RE.match(payload):
        return "a geo name may only contain letters, digits, underscore and hyphen"
    return None


def _validate_cidr(payload: str) -> str | None:
    if "/" not in payload:
        return "a prefix length is required, for example 10.0.0.0/8"
    try:
        ipaddress.ip_network(payload, strict=False)
    except ValueError as error:
        return f"not a valid IP network: {error}"
    return None


def _validate_asn(payload: str) -> str | None:
    if not payload.isdigit() or not 1 <= int(payload) <= _MAX_ASN:
        return f"an ASN must be a number between 1 and {_MAX_ASN}"
    return None


def _validate_port(payload: str) -> str | None:
    match = _PORT_RE.match(payload)
    if not match:
        return "expected a port or a port range such as 8000-8080"
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    if not 1 <= low <= _MAX_PORT or not 1 <= high <= _MAX_PORT:
        return f"ports must be between 1 and {_MAX_PORT}"
    if low > high:
        return "the start of a port range must not be greater than its end"
    return None


def _validate_process(payload: str) -> str | None:
    if not _PROCESS_RE.match(payload):
        return "a process name must not contain whitespace or a comma"
    return None


def _validate_network(payload: str) -> str | None:
    if payload not in ("tcp", "udp"):
        return "expected tcp or udp"
    return None


def _validate_rule_set(payload: str) -> str | None:
    # Membership in the configured providers is checked in `validate`, which has
    # the settings. Here we only reject shapes that cannot name a provider.
    return _validate_geo_name(payload)


RULE_TYPES: tuple[RuleTypeSpec, ...] = (
    RuleTypeSpec("DOMAIN", "example.com", "exact domain", False, _validate_domain),
    RuleTypeSpec("DOMAIN-SUFFIX", "example.com", "the domain and its subdomains", False, _validate_domain),
    RuleTypeSpec("DOMAIN-KEYWORD", "example", "any domain containing the keyword", False, _validate_keyword),
    RuleTypeSpec("DOMAIN-REGEX", r"^ad\..+\.com$", "domains matching the regular expression", False, _validate_regex),
    RuleTypeSpec("GEOSITE", "category-ads-all", "a domain set from the geosite database", False, _validate_geo_name),
    RuleTypeSpec("IP-CIDR", "10.0.0.0/8", "destination IP inside the network", True, _validate_cidr),
    RuleTypeSpec("IP-CIDR6", "2001:db8::/32", "destination IPv6 inside the network", True, _validate_cidr),
    RuleTypeSpec("IP-SUFFIX", "10.0.0.0/8", "destination IP with the trailing bits", True, _validate_cidr),
    RuleTypeSpec("IP-ASN", "13335", "destination IP announced by the AS number", True, _validate_asn),
    RuleTypeSpec("GEOIP", "CN", "destination IP in the country or region", True, _validate_geo_name),
    RuleTypeSpec("SRC-IP-CIDR", "192.168.1.0/24", "source IP inside the network", False, _validate_cidr),
    RuleTypeSpec("SRC-PORT", "8080", "source port or port range", False, _validate_port),
    RuleTypeSpec("DST-PORT", "443", "destination port or port range", False, _validate_port),
    RuleTypeSpec("NETWORK", "udp", "transport protocol", False, _validate_network),
    RuleTypeSpec("PROCESS-NAME", "curl", "name of the local process", False, _validate_process),
    RuleTypeSpec("PROCESS-PATH", "/usr/bin/curl", "path of the local process", False, _validate_process),
    RuleTypeSpec("RULE-SET", "anti-ad", "a rule provider declared in the template", True, _validate_rule_set),
)

RULE_TYPES_BY_NAME = {spec.name: spec for spec in RULE_TYPES}


def validate(rules: list[Rule], settings: Settings) -> list[ValidationError]:
    """Return every problem found in ``rules``, empty when they are safe to apply."""
    errors: list[ValidationError] = []

    if len(rules) > settings.max_rules:
        errors.append(ValidationError(f"{len(rules)} rules exceed the limit of {settings.max_rules}"))

    seen: dict[tuple[str, str], int] = {}
    for index, rule in enumerate(rules):
        errors.extend(_validate_rule(rule, index, settings))

        key = (rule.type.upper(), rule.payload)
        first = seen.setdefault(key, index)
        if first != index:
            errors.append(ValidationError(f"duplicate of rule {first + 1}", index))

    return errors


def _validate_rule(rule: Rule, index: int, settings: Settings) -> list[ValidationError]:
    errors: list[ValidationError] = []

    excluded = _EXCLUDED_TYPES.get(rule.type.upper())
    if excluded:
        return [ValidationError(f"{rule.type} is not allowed: {excluded}", index)]

    spec = RULE_TYPES_BY_NAME.get(rule.type)
    if spec is None:
        return [ValidationError(f"unknown rule type {rule.type!r}", index)]

    payload_error = spec.validate_payload(rule.payload)
    if payload_error:
        errors.append(ValidationError(f"{rule.type}: {payload_error}", index))
    elif spec.name == "RULE-SET" and rule.payload not in settings.rule_sets:
        known = ", ".join(sorted(settings.rule_sets)) or "none"
        errors.append(ValidationError(f"unknown rule provider {rule.payload!r}, the template declares: {known}", index))

    if rule.target not in settings.targets:
        known = ", ".join(sorted(settings.targets))
        errors.append(ValidationError(f"unknown target {rule.target!r}, expected one of: {known}", index))

    if rule.options:
        if rule.options != NO_RESOLVE:
            errors.append(ValidationError(f"unknown option {rule.options!r}, only {NO_RESOLVE} is supported", index))
        elif not spec.allows_no_resolve:
            errors.append(ValidationError(f"{NO_RESOLVE} only applies to IP based rules, not {rule.type}", index))

    return errors
