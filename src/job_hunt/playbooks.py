"""Playbook helpers shared by application.py and Phase 5 playbook authoring.

Phase 4 ships the tolerant loader that returns an empty list when the
playbook has no YAML frontmatter or no ``checkpoint_sequence`` field.
Phase 5 populates the frontmatter; Phase 9's ``check-integrity`` promotes
absence to a hard failure once every per-surface playbook has one.

Agents and Python consumers never parse the body — they read the
``checkpoint_sequence`` list and rely on record_attempt to enforce the DAG.
"""

from __future__ import annotations

from pathlib import Path

from .utils import parse_frontmatter, repo_root


def load_checkpoint_dag(playbook_path: str) -> list[str]:
    """Return the ``checkpoint_sequence`` from a playbook's YAML frontmatter.

    Accepts either an absolute or repo-relative path. Missing file or
    missing frontmatter → empty list (Phase 4 tolerant mode). Malformed
    frontmatter propagates as ValueError from the YAML parser.
    """
    if not playbook_path:
        return []
    path = Path(playbook_path)
    if not path.is_absolute():
        path = repo_root() / playbook_path
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    frontmatter, _ = parse_frontmatter(text)
    sequence = frontmatter.get("checkpoint_sequence", [])
    if not isinstance(sequence, list):
        return []
    return [str(s) for s in sequence]


def load_origin_allowlist(playbook_path: str) -> list[str]:
    """Same tolerant loader for the ``origin_allowlist`` frontmatter field.

    Phase 5 per-surface playbooks declare which hosts the agent may issue
    form_input / file_upload MCP calls against; Phase 4's tolerant mode
    returns an empty list (meaning "no origin guard enforced yet").
    """
    if not playbook_path:
        return []
    path = Path(playbook_path)
    if not path.is_absolute():
        path = repo_root() / playbook_path
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    frontmatter, _ = parse_frontmatter(text)
    allowlist = frontmatter.get("origin_allowlist", [])
    if not isinstance(allowlist, list):
        return []
    return [str(s) for s in allowlist]
