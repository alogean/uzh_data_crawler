"""The documentation of the taxonomy gate names everything the gate can do.

A command, a rule, an export format or a kind of change added to the code
without a word in docs/taxonomy.md and in the taxon-gate skill fails here.
"""

from pathlib import Path

import pytest

from uzh_crawler.gate.changes import CHANGES
from uzh_crawler.gate.checks import RULES
from uzh_crawler.gate.commands import ACTIONS
from uzh_crawler.gate.export import FORMATS

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "docs" / "taxonomy.md").read_text(encoding="utf-8")
SKILL = (ROOT / ".claude" / "skills" / "taxon-gate" / "SKILL.md").read_text(encoding="utf-8")


def test_the_actions_listed_are_the_actions_of_the_command():
    from uzh_crawler.cli import _parser

    parser = _parser()
    command = parser._subparsers._group_actions[0].choices["taxonomy"]
    actions = command._subparsers._group_actions[0].choices
    assert tuple(actions) == ACTIONS


@pytest.mark.parametrize("action", ACTIONS)
def test_every_action_is_documented(action):
    assert f"taxonomy {action}" in PAGE
    assert f"taxonomy {action}" in SKILL


@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_rule_is_documented(rule):
    assert f"`{rule}`" in PAGE


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_export_format_is_documented(fmt):
    assert f"--format {fmt}" in PAGE


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_every_kind_of_change_is_documented(change):
    assert f'"change": "{change}"' in PAGE
    assert f"`{change}`" in SKILL
