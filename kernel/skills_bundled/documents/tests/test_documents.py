import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import plugin

DOCX_BODY = (
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:r><w:t>First paragraph.</w:t></w:r></w:p><w:p/>'
    '<w:p><w:r><w:t>Third </w:t></w:r><w:r><w:t>one.</w:t></w:r></w:p>'
    '</w:body></w:document>'
)


class DocumentsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_page_ranges(self):
        self.assertEqual(plugin.page_numbers("1-3,7,2", 5), [1, 2, 3])
        self.assertEqual(plugin.page_numbers("", 2), [1, 2])
        with self.assertRaises(ValueError):
            plugin.page_numbers("3-1", 5)

    def test_csv_rows_keep_their_numbers(self):
        path = self.folder / "data.csv"
        path.write_text("name,age\nada,36\nalan,41\n", encoding="utf-8")
        result = plugin.csv_read(path, {"range": "2-3"})
        self.assertEqual([block["ref"] for block in result["blocks"]], ["row 2", "row 3"])
        self.assertEqual(result["blocks"][1]["values"], ["alan", "41"])

    def test_docx_paragraphs_keep_their_numbers(self):
        path = self.folder / "doc.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", DOCX_BODY)
        result = plugin.docx_read(path, {})
        self.assertEqual([(block["paragraph"], block["text"]) for block in result["blocks"]],
                         [(1, "First paragraph."), (3, "Third one.")])

    def test_unsupported_types_are_named(self):
        with self.assertRaises(ValueError):
            plugin.kind_of("slides.pptx")

    def test_long_results_say_where_to_continue(self):
        result = plugin.bounded({"blocks": [{"ref": f"row {n}", "text": "x" * 600} for n in range(1, 5)]}, 1000)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next"], "row 2")


if __name__ == "__main__":
    unittest.main()
