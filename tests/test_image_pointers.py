"""Regression: image files containing CDN URLs must embed cached image bytes."""
import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
import export_core as core

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

class ImagePointerTest(unittest.TestCase):
    def test_url_pointer_exports_real_image_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '123/Books'
            book = root / '1'
            for folder in ('Text', 'Images', 'com.hackemist.SDWebImageCache.cachedImages'):
                (book / folder).mkdir(parents=True)
            url = 'https://res.weread.qq.com/wrepub/epub_1_1'
            pointer = book / 'Images/formula.png'
            pointer.write_text(url)
            (book / 'Images/cover.png').write_bytes(PNG)
            digest = hashlib.md5(url.encode()).hexdigest()
            (book / 'com.hackemist.SDWebImageCache.cachedImages' / digest).write_bytes(PNG)
            (book / 'Text/chapter.xhtml').write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head><body><p>Logic</p><img src="../Images/formula.png"/><img src="' + url + '"/></body></html>')
            metadata = {'title': 'Pointer test', 'author': 'Test', 'publisher': '', 'chapters': [{'title': 'Chapter', 'level': 1, 'paths': ['Text/chapter.xhtml']}]}
            previous = core.BOOKS
            core.BOOKS = {'1': metadata}
            try:
                result = core.export_book('1', root, Path(temp) / 'output', Path(temp) / 'cache')
            finally:
                core.BOOKS = previous
            with zipfile.ZipFile(result['epub']) as archive:
                self.assertEqual(archive.read('OEBPS/Images/formula.png'), PNG)
                self.assertEqual(archive.read('OEBPS/Images/cover.png'), PNG)
                self.assertNotIn(b'https://', archive.read('OEBPS/Text/chapter.xhtml'))
                self.assertIsNone(archive.testzip())
            self.assertEqual(pointer.read_text(), url)
            self.assertEqual(result['missing_images'], 0)

if __name__ == '__main__':
    unittest.main()
