from __future__ import annotations

import unittest

from mflab_knowledge.verification import (
    attach_discovered_citations,
    claims_for_verification,
    emit_progress,
    normalize_support_discovery,
    normalize_verification,
    sanitize_fenced_code_blocks,
    supported_claim_subset,
)


class VerificationTests(unittest.TestCase):
    def test_progress_callback_failure_does_not_change_the_event(self) -> None:
        def disconnected(_event: dict[str, object]) -> None:
            raise RuntimeError("client disconnected")

        event = emit_progress(
            disconnected,
            stage="evidence",
            title="Evidence selected",
            data={"sources": 3},
        )

        self.assertEqual(event["stage"], "evidence")
        self.assertEqual(event["data"], {"sources": 3})

    def test_support_discovery_can_attach_only_validated_ids(self) -> None:
        answer = "The first operation advances state.\n\nA second claim is unknown."
        claims = claims_for_verification(answer)
        discovery = normalize_support_discovery(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "verdict": "supported",
                        "source_ids": ["S1", "S99"],
                        "finding": "Direct evidence.",
                    },
                    {
                        "claim_id": "C2",
                        "verdict": "unsupported",
                        "source_ids": [],
                        "finding": "Not established.",
                    },
                ]
            },
            claims=claims,
            valid_source_ids={"S1"},
        )

        cited, attached = attach_discovered_citations(answer, discovery)

        self.assertEqual(attached, 1)
        self.assertIn("advances state. [S1]", cited)
        self.assertNotIn("unknown. [", cited)

    def test_builds_claims_with_their_own_citations(self) -> None:
        claims = claims_for_verification(
            "The first operation happens here [S1].\n\n"
            "A different operation happens elsewhere [S2, S3]."
        )

        self.assertEqual([claim["claim_id"] for claim in claims], ["C1", "C2"])
        self.assertEqual(claims[0]["cited_source_ids"], ["S1"])
        self.assertEqual(claims[1]["cited_source_ids"], ["S2", "S3"])

    def test_splits_compound_paragraph_and_inherits_terminal_citation(self) -> None:
        claims = claims_for_verification(
            "The source measures memory. It does not establish collisions. [S2]"
        )

        self.assertEqual(len(claims), 2)
        self.assertEqual(
            [claim["cited_source_ids"] for claim in claims],
            [["S2"], ["S2"]],
        )
        self.assertEqual(
            claims[0]["text"], "The source measures memory. [S2]"
        )

    def test_keeps_distinct_sentence_citations_distinct(self) -> None:
        claims = claims_for_verification(
            "The first stage is local [S1]. The next stage is separate [S2]."
        )

        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]["cited_source_ids"], ["S1"])
        self.assertEqual(claims[1]["cited_source_ids"], ["S2"])

    def test_rejects_missing_claims_and_unknown_source_ids(self) -> None:
        claims = claims_for_verification(
            "The candidate initializes the complete system [S1].\n\n"
            "It also controls the mesh [S2]."
        )
        result = normalize_verification(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "verdict": "unsupported",
                        "source_ids": ["S1", "S999"],
                        "finding": "The source only assigns a local pointer.",
                    }
                ]
            },
            claims=claims,
            valid_source_ids={"S1", "S2"},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["claims"][0]["source_ids"], ["S1"])
        self.assertEqual(result["claims"][1]["verdict"], "uncertain")
        self.assertEqual(result["counts"]["unsupported"], 1)
        self.assertEqual(result["counts"]["uncertain"], 1)

    def test_accepts_json_fence_but_requires_every_claim(self) -> None:
        claims = claims_for_verification("The implementation is located here [S1].")
        result = normalize_verification(
            """```json
            {"claims":[{"claim_id":"C1","verdict":"supported","source_ids":["S1"],"finding":"The definition is present."}]}
            ```""",
            claims=claims,
            valid_source_ids={"S1"},
        )

        self.assertTrue(result["passed"])

    def test_accepts_valid_audit_object_wrapped_in_provider_text(self) -> None:
        claims = claims_for_verification("The implementation is here [S1].")
        result = normalize_verification(
            "Audit result follows:\n"
            '{"claims":[{"claim_id":"C1","verdict":"supported",'
            '"source_ids":["S1"],"finding":"The definition is present."}]}\n'
            "End of result.",
            claims=claims,
            valid_source_ids={"S1"},
        )

        self.assertTrue(result["passed"])

    def test_supported_verdict_must_reference_the_claims_own_source(self) -> None:
        claims = claims_for_verification(
            "First statement [S1].\n\nSecond statement [S2]."
        )
        result = normalize_verification(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "verdict": "supported",
                        "source_ids": ["S2"],
                        "finding": "Wrong source.",
                    },
                    {
                        "claim_id": "C2",
                        "verdict": "supported",
                        "source_ids": ["S2"],
                        "finding": "Direct support.",
                    },
                ]
            },
            claims=claims,
            valid_source_ids={"S1", "S2"},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["claims"][0]["verdict"], "uncertain")
        self.assertEqual(result["claims"][0]["source_ids"], [])

    def test_supported_subset_removes_rejected_claims_without_new_text(self) -> None:
        result = supported_claim_subset(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "claim": "The observed operation advances state [S1].",
                        "verdict": "supported",
                        "source_ids": ["S1"],
                    },
                    {
                        "claim_id": "C2",
                        "claim": "This is the complete architecture [S1].",
                        "verdict": "unsupported",
                        "source_ids": ["S1"],
                    },
                ]
            }
        )

        self.assertEqual(
            result,
            "The observed operation advances state [S1].",
        )

    def test_supported_subset_keeps_only_verbatim_code_from_approved_sources(
        self,
    ) -> None:
        verification = {
            "claims": [
                {
                    "claim_id": "C1",
                    "claim": "The operation advances state [S1].",
                    "verdict": "supported",
                    "source_ids": ["S1"],
                }
            ]
        }
        answer = (
            "The operation advances state [S1].\n\n"
            "```cpp\nstate.advance();\n```\n\n"
            "```cpp\ninvented_call();\n```"
        )
        result = supported_claim_subset(
            verification,
            answer=answer,
            sources=[
                {
                    "source_id": "S1",
                    "text": "void step() {\n    state.advance();\n}",
                }
            ],
        )

        self.assertIsNotNone(result)
        self.assertIn("```cpp\nstate.advance();\n```", str(result))
        self.assertIn("[S1]", str(result))
        self.assertNotIn("invented_call", str(result))

    def test_supported_subset_keeps_complete_lines_from_a_truncated_source(self) -> None:
        result = supported_claim_subset(
            {
                "claims": [
                    {
                        "claim_id": "C1",
                        "claim": "The operation advances state [S1].",
                        "verdict": "supported",
                        "source_ids": ["S1"],
                    }
                ]
            },
            answer=(
                "The operation advances state [S1].\n\n"
                "```cpp\nstate.advance();\n```"
            ),
            sources=[
                {
                    "source_id": "S1",
                    "text": "state.advance();",
                    "text_truncated": True,
                }
            ],
        )

        self.assertIn("```cpp\nstate.advance();\n```", str(result))

    def test_sanitizes_fenced_code_against_its_cited_complete_source(self) -> None:
        answer = (
            "Observed implementation [S1].\n\n"
            "```cpp\nstate.advance();\n```\n\n[S1]\n\n"
            "```cpp\ninvented();\n```\n\n[S1]"
        )
        sanitized, removed, attached = sanitize_fenced_code_blocks(
            answer,
            [{"source_id": "S1", "text": "state.advance();"}],
        )

        self.assertEqual(removed, 1)
        self.assertEqual(attached, 0)
        self.assertIn("state.advance", sanitized)
        self.assertNotIn("invented", sanitized)

    def test_sanitizer_keeps_complete_lines_from_truncated_context(self) -> None:
        sanitized, removed, attached = sanitize_fenced_code_blocks(
            "```cpp\nstate.advance();\n```\n\n[S1]",
            [
                {
                    "source_id": "S1",
                    "text": "state.advance();",
                    "text_truncated": True,
                }
            ],
        )

        self.assertEqual(removed, 0)
        self.assertEqual(attached, 0)
        self.assertIn("```cpp\nstate.advance();\n```", sanitized)

    def test_sanitizer_rejects_a_clipped_line_from_truncated_context(self) -> None:
        sanitized, removed, attached = sanitize_fenced_code_blocks(
            "```cpp\nstate.advan\n```\n\n[S1]",
            [
                {
                    "source_id": "S1",
                    "text": "state.advance();\n... [trecho intermediário omitido] ...",
                    "text_truncated": True,
                }
            ],
        )

        self.assertEqual(removed, 1)
        self.assertEqual(attached, 0)
        self.assertNotIn("```", sanitized)

    def test_sanitizer_attaches_the_only_exact_source_to_uncited_code(self) -> None:
        sanitized, removed, attached = sanitize_fenced_code_blocks(
            "```cpp\nstate.advance();\n```",
            [{"source_id": "S4", "text": "state.advance();"}],
        )

        self.assertEqual(removed, 0)
        self.assertEqual(attached, 1)
        self.assertEqual(sanitized, "```cpp\nstate.advance();\n```\n\n[S4]")

    def test_sanitizer_rejects_ambiguous_uncited_code(self) -> None:
        sanitized, removed, attached = sanitize_fenced_code_blocks(
            "```cpp\nreturn true;\n```",
            [
                {"source_id": "S1", "text": "return true;"},
                {"source_id": "S2", "text": "return true;"},
            ],
        )

        self.assertEqual(removed, 1)
        self.assertEqual(attached, 0)
        self.assertNotIn("```", sanitized)


if __name__ == "__main__":
    unittest.main()
