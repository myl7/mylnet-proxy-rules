"""Tests for rule validation."""

import unittest
from dataclasses import replace
from pathlib import Path

from app.rules import Rule, parse
from app.validate import validate
from tests.settings import build_settings

FIXTURE = Path(__file__).parent / "data" / "clash_rules_extend.yaml"

SETTINGS = build_settings(max_rules=10)


def messages(rules: list[Rule]) -> list[str]:
    return [error.message for error in validate(rules, SETTINGS)]


class ValidateTest(unittest.TestCase):
    def test_accepts_a_plain_rule(self) -> None:
        self.assertEqual(messages([Rule(type="DOMAIN-SUFFIX", payload="example.com", target="proxy")]), [])

    def test_accepts_the_fixture(self) -> None:
        rules = parse(FIXTURE.read_text(encoding="utf-8"))
        settings = replace(SETTINGS, max_rules=2000)
        self.assertEqual([error.message for error in validate(rules, settings)], [])

    def test_rejects_an_unknown_type(self) -> None:
        errors = messages([Rule(type="DOMAIN-WILDCARD", payload="*.example.com", target="proxy")])
        self.assertIn("unknown rule type", errors[0])

    def test_rejects_match(self) -> None:
        errors = messages([Rule(type="MATCH", payload="x", target="proxy")])
        self.assertIn("shadow every rule", errors[0])

    def test_rejects_logic_rules(self) -> None:
        errors = messages([Rule(type="AND", payload="((DOMAIN", target="proxy")])
        self.assertIn("nest commas", errors[0])

    def test_rejects_an_unknown_target(self) -> None:
        errors = messages([Rule(type="DOMAIN", payload="example.com", target="typo")])
        self.assertIn("unknown target", errors[0])

    def test_rejects_a_bad_domain(self) -> None:
        errors = messages([Rule(type="DOMAIN", payload="not a domain", target="proxy")])
        self.assertIn("not a valid domain", errors[0])

    def test_rejects_a_wildcard_domain(self) -> None:
        errors = messages([Rule(type="DOMAIN-SUFFIX", payload="*.example.com", target="proxy")])
        self.assertIn("wildcards", errors[0])

    def test_rejects_a_cidr_without_a_prefix(self) -> None:
        errors = messages([Rule(type="IP-CIDR", payload="10.0.0.1", target="DIRECT")])
        self.assertIn("prefix length", errors[0])

    def test_accepts_a_cidr_with_no_resolve(self) -> None:
        rule = Rule(type="IP-CIDR", payload="10.0.0.0/8", target="DIRECT", options="no-resolve")
        self.assertEqual(messages([rule]), [])

    def test_rejects_no_resolve_on_a_domain_rule(self) -> None:
        rule = Rule(type="DOMAIN", payload="example.com", target="proxy", options="no-resolve")
        self.assertIn("only applies to IP based rules", messages([rule])[0])

    def test_rejects_an_unknown_option(self) -> None:
        rule = Rule(type="IP-CIDR", payload="10.0.0.0/8", target="DIRECT", options="src")
        self.assertIn("unknown option", messages([rule])[0])

    def test_rejects_an_undeclared_rule_provider(self) -> None:
        errors = messages([Rule(type="RULE-SET", payload="my-list", target="REJECT")])
        self.assertIn("unknown rule provider", errors[0])

    def test_accepts_the_declared_rule_provider(self) -> None:
        self.assertEqual(messages([Rule(type="RULE-SET", payload="anti-ad", target="REJECT")]), [])

    def test_rejects_a_bad_asn(self) -> None:
        self.assertIn("ASN", messages([Rule(type="IP-ASN", payload="AS13335", target="ai")])[0])

    def test_rejects_a_bad_port_range(self) -> None:
        self.assertIn("must not be greater", messages([Rule(type="DST-PORT", payload="90-80", target="proxy")])[0])

    def test_reports_duplicates_with_the_first_index(self) -> None:
        rules = [
            Rule(type="DOMAIN", payload="example.com", target="proxy"),
            Rule(type="DOMAIN", payload="example.com", target="cn"),
        ]
        errors = validate(rules, SETTINGS)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].index, 1)
        self.assertIn("duplicate of rule 1", errors[0].message)

    def test_reports_the_rule_limit(self) -> None:
        rules = [Rule(type="DOMAIN", payload=f"host{index}.example.com", target="proxy") for index in range(11)]
        self.assertTrue(any("exceed the limit" in message for message in messages(rules)))


if __name__ == "__main__":
    unittest.main()
