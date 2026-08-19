from __future__ import annotations

import unittest

from mflab_knowledge.grounding import citation_coverage, citation_ids


class GroundingTests(unittest.TestCase):
    def test_extracts_ids_and_scores_paragraphs_and_bullets(self) -> None:
        answer = """# Result

The first fact is supported [S1].

- Supported bullet [S2].
- Unsupported factual bullet.

```cpp
run_without_citation();
```
"""
        result = citation_coverage(
            answer,
            valid_source_ids={"S1", "S2"},
        )

        self.assertEqual(citation_ids(answer), {"S1", "S2"})
        self.assertEqual(result["units"], 3)
        self.assertEqual(result["cited_units"], 2)
        self.assertAlmostEqual(float(result["coverage"]), 2 / 3)
        self.assertEqual(len(result["uncited_previews"]), 1)

    def test_invalid_citation_does_not_count_toward_coverage(self) -> None:
        result = citation_coverage(
            "A factual statement [S99].",
            valid_source_ids={"S1"},
        )

        self.assertEqual(result["coverage"], 0.0)

    def test_accepts_strict_grouped_citations(self) -> None:
        self.assertEqual(
            citation_ids("Supported by [S1, S2] and [S3; S4]."),
            {"S1", "S2", "S3", "S4"},
        )
        self.assertEqual(citation_ids("Not citations: [S1 text] or S2."), set())


if __name__ == "__main__":
    unittest.main()
