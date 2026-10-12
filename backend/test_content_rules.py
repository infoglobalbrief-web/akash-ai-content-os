import unittest
from app.content_pipeline import calculate_seo_checks, slugify


class ContentPipelineRuleTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("New AI Model: Pricing & Features"), "new-ai-model-pricing-features")

    def test_seo_flags_short_content(self):
        article = {
            "title": "AI update",
            "seo_title": "AI update",
            "meta_description": "A short description.",
            "focus_keyword": "AI update",
            "content_markdown": "## Overview\n\nBrief content [1].\n\n## Sources\n\n[1] https://example.com",
        }
        score, word_count, checks = calculate_seo_checks(article, 1)
        self.assertEqual(word_count < 300, True)
        self.assertFalse(checks["at_least_300_words"]["passed"])
        self.assertTrue(checks["evidence_citations_present"]["passed"])
        self.assertTrue(0 <= score <= 100)

    def test_missing_sources_fails_check(self):
        article = {
            "title": "A sufficiently long title for testing article scoring",
            "seo_title": "A sufficiently long title for testing article scoring",
            "meta_description": "This meta description is long enough to test SEO metadata but does not have to be perfect because it is a unit test for the scoring behavior.",
            "focus_keyword": "testing article",
            "content_markdown": "## Overview\n\n" + ("testing article and evidence. " * 100),
        }
        _, _, checks = calculate_seo_checks(article, 1)
        self.assertFalse(checks["sources_section_present"]["passed"])

    def test_keyword_presence(self):
        article = {
            "title": "API pricing update",
            "seo_title": "API pricing update for developers and teams",
            "meta_description": "Review an API pricing update and the source notes that developers should confirm before making any decisions about usage and costs.",
            "focus_keyword": "API pricing",
            "content_markdown": "## Details\n\nAPI pricing was mentioned.\n\n## Sources\n\n[1] https://example.com",
        }
        _, _, checks = calculate_seo_checks(article, 1)
        self.assertTrue(checks["keyword_in_title_or_content"]["passed"])


if __name__ == "__main__":
    unittest.main()
