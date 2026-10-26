"""The policy wording and the claims manual, served by section."""

import re
from typing import Literal

from backend import queries
from backend.db import DATA
from backend.models import Section, SectionRef

DocumentName = Literal["wording", "manual"]

FILES: dict[DocumentName, str] = {
    "wording": "policy_wording.md",
    "manual": "claims_manual.md",
}
LABELS: dict[DocumentName, str] = {
    "wording": "Policy wording",
    "manual": "Claims manual",
}
HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)


def sections(document: DocumentName) -> list[Section]:
    """Split a document at its numbered headings."""
    text = (DATA / FILES[document]).read_text()
    found = list(HEADING.finditer(text))
    ends = [match.start() for match in found[1:]] + [len(text)]
    return [
        Section(
            document=document,
            number=int(match.group(1)),
            title=match.group(2),
            text=text[match.start() : end].strip(),
        )
        for match, end in zip(found, ends, strict=True)
    ]


def list_sections(document: DocumentName) -> list[SectionRef]:
    """List the numbered sections of a document."""
    return [SectionRef(number=s.number, title=s.title) for s in sections(document)]


def get_section(document: DocumentName, number: int) -> Section:
    """Return one section of a document, or raise a 404."""
    for section in sections(document):
        if section.number == number:
            return section
    raise queries.not_found(f"section of the {LABELS[document].lower()}", str(number))


def is_section(label: str) -> bool:
    """Say whether a label such as "3. Trip Delay" names a section."""
    return any(
        f"{section.number}. {section.title}" == label
        for document in FILES
        for section in sections(document)
    )
