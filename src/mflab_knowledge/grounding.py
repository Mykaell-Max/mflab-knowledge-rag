from __future__ import annotations

import re

CITATION_GROUP_PATTERN = re.compile(
    r"\[\s*S\d+\s*(?:(?:,|;)\s*S\d+\s*)*\]"
)
CITATION_ID_PATTERN = re.compile(r"S(\d+)")
LIST_ITEM_PATTERN = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")


def citation_ids(text: str) -> set[str]:
    return {
        f"S{value}"
        for group in CITATION_GROUP_PATTERN.findall(text)
        for value in CITATION_ID_PATTERN.findall(group)
    }


def _factual_units(text: str) -> list[str]:
    units: list[str] = []
    paragraph: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        if paragraph:
            units.append(" ".join(paragraph).strip())
            paragraph.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            flush_paragraph()
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not line:
            flush_paragraph()
            continue
        if line.startswith("#"):
            flush_paragraph()
            continue
        if LIST_ITEM_PATTERN.match(line):
            flush_paragraph()
            units.append(line)
            continue
        paragraph.append(line)
    flush_paragraph()

    selected: list[str] = []
    for unit in units:
        plain = re.sub(r"[`*_>#]", "", unit).strip()
        if len(plain) < 12 or not any(character.isalpha() for character in plain):
            continue
        if plain.endswith(":") and len(plain) <= 60:
            continue
        selected.append(unit)
    return selected


def citation_coverage(
    answer: str,
    *,
    valid_source_ids: set[str],
) -> dict[str, object]:
    units = _factual_units(answer)
    cited = [
        unit
        for unit in units
        if citation_ids(unit).intersection(valid_source_ids)
    ]
    uncited = [unit for unit in units if unit not in cited]
    return {
        "units": len(units),
        "cited_units": len(cited),
        "coverage": (len(cited) / len(units) if units else None),
        "uncited_previews": [unit[:200] for unit in uncited[:5]],
    }
