from __future__ import annotations

import unittest

from mflab_knowledge.verification import (
    attach_discovered_citations,
    claims_for_verification,
    emit_progress,
    normalize_support_discovery,
    normalize_verification,
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


if __name__ == "__main__":
    unittest.main()
