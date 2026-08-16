"""Parse and serialise the mihomo rule fragment shared with the Ansible repo.

The fragment lives in ``mylnet-ansible-secrets/clash_rules_extend.yaml`` and is
injected into the rendered subscription by ``playbooks/templates/proxy/clash.yaml.j2``::

    {{ lookup('ansible.builtin.file', 'secrets/clash_rules_extend.yaml').split('\\n')[1:] | join('\\n') }}

That expression slices the file as plain text. It drops the first line and pastes
the rest into the surrounding ``rules:`` list, so the file is not free-form YAML.
The first line has to be exactly ``rules:`` and every other line has to be a
two-space indented list item. ``dump`` guarantees both, and ``tests/test_rules.py``
asserts that the committed file round-trips byte for byte.

Full-line comments are rejected rather than silently dropped, because the model
here has nowhere to keep them. Per-rule notes are kept as inline YAML comments,
which mihomo discards when it parses the rendered subscription.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADER = "rules:"
INDENT = "  - "

# YAML only starts an inline comment when the "#" is preceded by whitespace.
NOTE_SEPARATOR = " #"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class RuleFormatError(ValueError):
    """Raised when the rule file does not match the format the template needs."""

    def __init__(self, message: str, line_number: int | None = None) -> None:
        self.line_number = line_number
        super().__init__(f"line {line_number}: {message}" if line_number else message)


@dataclass(frozen=True, slots=True)
class Rule:
    """One mihomo rule, split into the fields the UI edits."""

    type: str
    payload: str
    target: str
    # Trailing modifier such as "no-resolve". Empty when the rule has none.
    options: str = ""
    # Human note kept as an inline YAML comment. Not seen by mihomo.
    note: str = ""

    @property
    def text(self) -> str:
        """Return the rule as mihomo reads it, without the note."""
        fields = [self.type, self.payload, self.target]
        if self.options:
            fields.append(self.options)
        return ",".join(fields)

    @property
    def line(self) -> str:
        """Return the rule as one line of the rule file, without the newline."""
        line = INDENT + self.text
        if self.note:
            line += f"{NOTE_SEPARATOR} {self.note}"
        return line


def parse(text: str) -> list[Rule]:
    """Parse the rule file into rules, raising ``RuleFormatError`` on anything unexpected."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != HEADER:
        raise RuleFormatError(f"the first line must be {HEADER!r}", 1)

    rules: list[Rule] = []
    for offset, raw in enumerate(lines[1:], start=2):
        if not raw.strip():
            continue
        rules.append(_parse_line(raw, offset))
    return rules


def _parse_line(raw: str, line_number: int) -> Rule:
    if raw.lstrip().startswith("#"):
        raise RuleFormatError("full-line comments are not supported, use a note after the rule", line_number)
    if not raw.startswith(INDENT):
        raise RuleFormatError(f"expected a list item indented with {INDENT!r}", line_number)

    body, separator, note = raw[len(INDENT) :].partition(NOTE_SEPARATOR)
    note = note.strip() if separator else ""

    fields = [field.strip() for field in body.split(",")]
    if len(fields) not in (3, 4):
        raise RuleFormatError(f"expected 3 or 4 comma separated fields, got {len(fields)}", line_number)
    if any(not field for field in fields):
        raise RuleFormatError("fields must not be empty", line_number)

    rule_type, payload, target = fields[0], fields[1], fields[2]
    options = fields[3] if len(fields) == 4 else ""
    return Rule(type=rule_type, payload=payload, target=target, options=options, note=note)


def dump(rules: list[Rule]) -> str:
    """Serialise rules back into the file format the template slices."""
    for rule in rules:
        _check_serialisable(rule)
    return "".join([f"{HEADER}\n", *(f"{rule.line}\n" for rule in rules)])


def _check_serialisable(rule: Rule) -> None:
    """Reject values that would produce a file ``parse`` cannot read back."""
    for name, value in (
        ("type", rule.type),
        ("payload", rule.payload),
        ("target", rule.target),
        ("options", rule.options),
        ("note", rule.note),
    ):
        if _CONTROL_RE.search(value):
            raise RuleFormatError(f"{name} must not contain control characters or newlines")
        if value != value.strip():
            raise RuleFormatError(f"{name} must not have leading or trailing whitespace")
    for name, value in (("type", rule.type), ("payload", rule.payload), ("target", rule.target)):
        if not value:
            raise RuleFormatError(f"{name} must not be empty")
        if "," in value:
            raise RuleFormatError(f"{name} must not contain a comma")
        if NOTE_SEPARATOR in value:
            raise RuleFormatError(f"{name} must not contain {NOTE_SEPARATOR!r}, it would start a YAML comment")
    if "," in rule.options or NOTE_SEPARATOR in rule.options:
        raise RuleFormatError("options must not contain a comma or start a YAML comment")
