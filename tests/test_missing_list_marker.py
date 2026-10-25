"""Missing decorative list images can fall back; required resources cannot."""
from pathlib import Path
import tempfile
import unittest
import zipfile
import export_core as core

class ListMarkerTest(unittest.TestCase):
    def export(self, root, style):
        book = root / '123/Books/1'
        (book / 'Text').mkdir(parents=True)
        (book / 'Styles').mkdir()
        (book / 'Styles/style.css').write_text(style)
        (book / 'Text/ch.xhtml').write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title><link href="../Styles/style.css"/></head><body><ul class="listStyleImg"><li>Keep this item</li></ul></body></html>')
        core.BOOKS = {'1': {'title': 'Test', 'author': '', 'publisher': '', 'chapters': [{'title': 'Test', 'level': 1, 'paths': ['Text/ch.xhtml']}]}}
        return core.export_book('1', book.parent, root / 'output', root / 'cache')

    def test_missing_list_marker_uses_css_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.export(Path(tmp), '.listStyleImg {list-style-type: square; list-style-image: url(../Images/list.png); color: red;}')
            with zipfile.ZipFile(result['epub']) as z:
                css = z.read('OEBPS/Styles/style.css')
                self.assertIn(b'list-style-image: none', css)
                self.assertIn(b'list-style-type: square', css)
                self.assertIn(b'Keep this item', z.read('OEBPS/Text/ch.xhtml'))
            self.assertEqual(result['normalized_missing_list_images'], 1)

    def test_missing_background_image_is_still_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, 'Missing stylesheet resource'):
            self.export(Path(tmp), 'body {background-image: url(../Images/required.png);}')

if __name__ == '__main__':
    unittest.main()
