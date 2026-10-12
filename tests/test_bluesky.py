import unittest

from src import bluesky


class FacetsTest(unittest.TestCase):
    def test_url_facet_covers_complete_ascii_url(self):
        text = "Veja https://example.com/a no post"
        facet = bluesky._facets(text)[0]
        window = text.encode()[facet["index"]["byteStart"]:facet["index"]["byteEnd"]]
        self.assertEqual(window.decode(), "https://example.com/a")

    def test_url_facet_uses_utf8_byte_offsets(self):
        text = "Veja https://example.com/á no post"
        facet = bluesky._facets(text)[0]
        window = text.encode()[facet["index"]["byteStart"]:facet["index"]["byteEnd"]]
        self.assertEqual(window.decode(), "https://example.com/á")


if __name__ == "__main__":
    unittest.main()
