"""Live Jev adapter tests. Set TYPESAFE_API_KEY to run one shared request."""

import os
import unittest

from futuresim_agents.minimalHarnessAgent.evidence_analysis import TypeSafeEvidenceClient


@unittest.skipUnless(os.environ.get("TYPESAFE_API_KEY", "").strip(), "TYPESAFE_API_KEY is required")
class LiveEvidenceAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Send one explicit launch report through the adapter for all assertions."""
        adapter = TypeSafeEvidenceClient()
        cls.addClassCleanup(adapter.close)
        cls.result = adapter.analyze(
            question={
                "qid": "atlas_001",
                "title": "Will Project Atlas launch publicly by January 2, 2026?",
                "background": "Project Atlas is a synthetic software project.",
                "resolution_criteria": "A public launch on or before January 2 counts.",
            },
            evidence_text="Project Atlas launched publicly on January 1, 2026. It is now available to everyone.",
            sim_date="2026-01-01",
            source_id="synthetic_launch_report",
        )

    def assert_distribution(self, probabilities, labels):
        """Check that each expected label has a probability and the values sum to one."""
        self.assertEqual(set(probabilities), labels)
        for label, value in probabilities.items():
            with self.subTest(label=label):
                self.assertGreaterEqual(value, 0)
                self.assertLessEqual(value, 1)
        self.assertAlmostEqual(sum(probabilities.values()), 1, delta=0.01)

    def test_relevance(self):
        relevance = self.result["signals"]["relevance"]
        self.assertGreaterEqual(relevance["score"], 2, "The passage directly reports the launch.")
        self.assertLessEqual(relevance["score"], 3)
        self.assertGreaterEqual(relevance["confidence"], 0)
        self.assertLessEqual(relevance["confidence"], 1)
        self.assert_distribution(relevance["probabilities"], {"0", "1", "2", "3"})

    def test_direction(self):
        direction = self.result["signals"]["direction"]
        self.assertEqual(direction["choice"], "supports")
        self.assertGreaterEqual(direction["confidence"], 0)
        self.assertLessEqual(direction["confidence"], 1)
        self.assert_distribution(direction["probabilities"], {"supports", "opposes", "mixed", "neutral"})

    def test_explicit_confirmation(self):
        probability = self.result["signals"]["outcome_confirmed"]["probability_yes"]
        self.assertGreater(probability, 0.5, "The passage explicitly says that Atlas launched.")
        self.assertLessEqual(probability, 1)

    def test_model_and_usage(self):
        self.assertIsInstance(self.result["model"], str)
        self.assertTrue(self.result["model"].strip())
        usage = self.result["usage"]
        self.assertIsInstance(usage, dict)
        for field in ("input_tokens", "output_tokens"):
            if field in usage:
                with self.subTest(field=field):
                    self.assertIsInstance(usage[field], int)
                    self.assertGreaterEqual(usage[field], 0)
