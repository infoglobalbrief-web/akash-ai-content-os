"""Small offline regression tests for rule-based discovery classification."""
import unittest

from app.intelligence import classify_with_rules


class RuleClassifierTests(unittest.TestCase):
    def test_pricing_change(self):
        result = classify_with_rules(
            "Anthropic announces lower API pricing per token for Claude",
            "API pricing was reduced, with lower cost per token.",
            "https://example.com/pricing",
        )
        self.assertEqual(result["category"], "pricing")
        self.assertEqual(result["event_type"], "pricing_change")
        self.assertGreaterEqual(result["importance_score"], 55)

    def test_model_release(self):
        result = classify_with_rules(
            "OpenAI releases a new GPT model",
            "A new AI model is now available.",
            "https://example.com/model",
        )
        self.assertEqual(result["event_type"], "new_model")
        self.assertEqual(result["provider_name"], "OpenAI")

    def test_seo_category(self):
        result = classify_with_rules(
            "WordPress SEO guide: improve Google Discover traffic",
            "A tutorial about content marketing and search ranking.",
            "https://example.com/seo",
        )
        self.assertEqual(result["category"], "marketing_seo")
        self.assertEqual(result["event_type"], "marketing_seo_update")

    def test_scores_are_bounded(self):
        result = classify_with_rules("Unrelated community post", "A local event.")
        self.assertGreaterEqual(result["relevance_score"], 0)
        self.assertLessEqual(result["relevance_score"], 100)
        self.assertGreaterEqual(result["importance_score"], 0)
        self.assertLessEqual(result["importance_score"], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
