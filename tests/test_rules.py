"""Tests for the rule file format.

The round-trip test is the important one. ``clash.yaml.j2`` slices the file as
plain text, so any change to ``dump`` that alters the header or the indentation
would produce a broken subscription rather than a Python error.
"""

import unittest
from pathlib import Path

import yaml

from app.apply import check_renders
from app.rules import Rule, RuleFormatError, dump, parse

FIXTURE = Path(__file__).parent / "data" / "clash_rules_extend.yaml"
# The file the deployed app actually edits, when this checkout sits next to it.
LIVE_FILE = Path(__file__).parents[2] / "mylnet-ansible-secrets" / "clash_rules_extend.yaml"


class ParseTest(unittest.TestCase):
    def test_parses_the_fixture(self) -> None:
        rules = parse(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 17)
        self.assertEqual(rules[0], Rule(type="DOMAIN-SUFFIX", payload="example.com", target="proxy"))
        self.assertEqual(rules[-1].payload, "two.example.net")

    def test_parses_a_note(self) -> None:
        rules = parse("rules:\n  - DOMAIN-SUFFIX,example.com,proxy # blocked at home\n")
        self.assertEqual(rules[0].note, "blocked at home")
        self.assertEqual(rules[0].text, "DOMAIN-SUFFIX,example.com,proxy")

    def test_parses_options(self) -> None:
        rules = parse("rules:\n  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve\n")
        self.assertEqual(rules[0].options, "no-resolve")

    def test_rejects_a_wrong_header(self) -> None:
        with self.assertRaises(RuleFormatError):
            parse("rule:\n  - DOMAIN,example.com,proxy\n")

    def test_rejects_a_missing_indent(self) -> None:
        with self.assertRaises(RuleFormatError):
            parse("rules:\n- DOMAIN,example.com,proxy\n")

    def test_rejects_a_full_line_comment(self) -> None:
        with self.assertRaises(RuleFormatError) as caught:
            parse("rules:\n  # adult sites\n  - DOMAIN,example.com,proxy\n")
        self.assertIn("full-line comments", str(caught.exception))

    def test_rejects_a_wrong_field_count(self) -> None:
        with self.assertRaises(RuleFormatError):
            parse("rules:\n  - DOMAIN,example.com\n")


class DumpTest(unittest.TestCase):
    def test_round_trips_the_fixture_byte_for_byte(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertEqual(dump(parse(text)), text)

    def test_round_trips_the_live_file_byte_for_byte(self) -> None:
        if not LIVE_FILE.exists():
            self.skipTest(f"{LIVE_FILE} is not checked out next to this repo")
        text = LIVE_FILE.read_text(encoding="utf-8")
        self.assertEqual(dump(parse(text)), text)

    def test_round_trips_notes_and_options(self) -> None:
        text = "rules:\n  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve # lan # really\n"
        self.assertEqual(dump(parse(text)), text)

    def test_starts_with_the_header_the_template_drops(self) -> None:
        text = dump([Rule(type="DOMAIN", payload="example.com", target="proxy")])
        self.assertEqual(text.split("\n")[0], "rules:")
        self.assertEqual(text.split("\n")[1], "  - DOMAIN,example.com,proxy")

    def test_dumps_an_empty_list(self) -> None:
        self.assertEqual(dump([]), "rules:\n")

    def test_rejects_a_newline(self) -> None:
        with self.assertRaises(RuleFormatError):
            dump([Rule(type="DOMAIN", payload="a\nb", target="proxy")])

    def test_rejects_a_comma_inside_a_field(self) -> None:
        with self.assertRaises(RuleFormatError):
            dump([Rule(type="DOMAIN", payload="a,b", target="proxy")])

    def test_rejects_a_comment_start_inside_a_field(self) -> None:
        with self.assertRaises(RuleFormatError):
            dump([Rule(type="DOMAIN", payload="a #b", target="proxy")])


class RenderTest(unittest.TestCase):
    def test_the_fixture_renders(self) -> None:
        self.assertIsNone(check_renders(FIXTURE.read_text(encoding="utf-8")))

    def test_the_injected_fragment_keeps_every_rule(self) -> None:
        # Reproduces the slice in playbooks/templates/proxy/clash.yaml.j2.
        text = FIXTURE.read_text(encoding="utf-8")
        fragment = "\n".join(text.split("\n")[1:])
        document = yaml.safe_load(f"rules:\n{fragment}")
        self.assertEqual(document["rules"], [rule.text for rule in parse(text)])

    def test_reports_bad_indentation(self) -> None:
        self.assertIsNotNone(check_renders("rules:\n      - DOMAIN,example.com,proxy\n    bad: mapping\n"))


if __name__ == "__main__":
    unittest.main()
