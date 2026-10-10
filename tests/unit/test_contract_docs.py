"""The contract documents exist and keep the structure the other units rely on."""

import re
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts"

CONTRACT_DOCUMENTS = (
    "chunk-id.md",
    "configuration.md",
    "observability.md",
    "schema.md",
    "queue.md",
    "idempotency.md",
    "storage.md",
)

CHANGE_LOG_HEADING = "Change log"
# A change log entry is a table row (or a plain line) that starts with an ISO date.
_DATED_ENTRY = re.compile(r"\|?\s*\d{4}-\d{2}-\d{2}\b")


def _level_2_headings(text: str) -> list[tuple[int, str]]:
    """Line index and text of every `## ` heading outside fenced code blocks.

    A `## ` line inside a fence is a comment in a code sample, not a heading.
    """
    headings = []
    in_fence = False
    for index, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            headings.append((index, line[3:].strip()))
    return headings


def test_contract_documents_and_readme_exist() -> None:
    """All seven documents and README.md are files in docs/contracts/."""
    expected = (*CONTRACT_DOCUMENTS, "README.md")
    missing = [name for name in expected if not (CONTRACTS_DIR / name).is_file()]
    assert not missing, f"missing from {CONTRACTS_DIR}: {missing}"


@pytest.mark.parametrize("name", CONTRACT_DOCUMENTS)
def test_contract_document_ends_with_change_log(name: str) -> None:
    """The last level-2 heading is exactly `## Change log`, and the section
    under it holds at least one entry that starts with an ISO date."""
    text = (CONTRACTS_DIR / name).read_text(encoding="utf-8")
    headings = _level_2_headings(text)
    assert headings, f"{name} has no level-2 heading"

    last_index, last_heading = headings[-1]
    assert last_heading == CHANGE_LOG_HEADING, (
        f"the last level-2 heading of {name} is {last_heading!r}, "
        f"not {CHANGE_LOG_HEADING!r}"
    )

    entries = [
        line for line in text.splitlines()[last_index + 1 :] if _DATED_ENTRY.match(line)
    ]
    assert entries, (
        f"the change log of {name} has no entry that starts with an ISO date"
    )


def test_readme_names_every_contract_document() -> None:
    """README.md mentions each of the seven file names."""
    readme = (CONTRACTS_DIR / "README.md").read_text(encoding="utf-8")
    unnamed = [name for name in CONTRACT_DOCUMENTS if name not in readme]
    assert not unnamed, f"README.md does not name: {unnamed}"
