import unittest

from app.wordpress_publisher import build_post_payload, render_markdown, stable_post_slug


class WordPressPublisherTests(unittest.TestCase):
    def test_slug_is_stable_and_unique_per_draft(self):
        slug_a = stable_post_slug(42, "my-test-post", "A title")
        slug_b = stable_post_slug(42, "my-test-post", "A title")
        slug_c = stable_post_slug(43, "my-test-post", "A title")
        self.assertEqual(slug_a, slug_b)
        self.assertNotEqual(slug_a, slug_c)
        self.assertLessEqual(len(slug_a), 220)
        self.assertTrue(slug_a.endswith("-akcos-42"))

    def test_markdown_renders_headings_and_lists(self):
        html = render_markdown("## Heading\n\n- one\n- two")
        self.assertIn("<h2>Heading</h2>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li>one</li>", html)

    def test_markdown_sanitization_removes_script_and_javascript_links(self):
        html = render_markdown(
            'Safe text\n\n<script>alert("bad")</script>\n\n[bad](javascript:alert(1))'
        )
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("javascript:", html.lower())
        self.assertIn("Safe text", html)

    def test_payload_contains_rank_math_and_idempotency_metadata(self):
        draft = {
            "id": 9,
            "title": "Example article",
            "slug": "example-article",
            "seo_title": "Example SEO title",
            "meta_description": "An example description.",
            "focus_keyword": "example keyword",
            "content_markdown": "## Intro\n\nHello **world**.",
        }
        publication = {
            "requested_status": "draft",
            "idempotency_key": "akcos-content-draft-9",
            "category_ids": [2],
            "tag_ids": [3, 4],
        }
        payload = build_post_payload(draft, publication)
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["categories"], [2])
        self.assertEqual(payload["tags"], [3, 4])
        self.assertEqual(payload["meta"]["rank_math_title"], "Example SEO title")
        self.assertEqual(payload["meta"]["rank_math_description"], "An example description.")
        self.assertEqual(payload["meta"]["rank_math_focus_keyword"], "example keyword")
        self.assertEqual(payload["meta"]["akcos_draft_id"], 9)
        self.assertEqual(payload["meta"]["akcos_idempotency_key"], "akcos-content-draft-9")
        self.assertIn("<strong>world</strong>", payload["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
