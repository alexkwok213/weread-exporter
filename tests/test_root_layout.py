"""Root-level XHTML must be discoverable and export with working resource links."""
import base64
import hashlib
import plistlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
import worker

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

class RootLayoutTest(unittest.TestCase):
    def test_root_chapters_export_with_styles_cover_and_images(self):
        self.check_export('cover.xhtml', 'css/style.css')

    def test_html_chapters_are_discovered_and_exported(self):
        self.check_export('Text/coverpage.html', 'Styles/style.css')

    def test_translation_styles_are_packaged_and_tracked(self):
        self.check_export('Text/coverpage.html', 'wereadstyle/wrtranslation.css')

    def check_export(self, chapter_path, style_path):
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            data = tmp / 'Data'
            book = data / 'Library/123/Books/1'
            (book / style_path).parent.mkdir(parents=True)
            (book / chapter_path).parent.mkdir(parents=True, exist_ok=True)
            (data / 'Documents').mkdir()
            image_cache = book / 'com.hackemist.SDWebImageCache.cachedImages'
            image_cache.mkdir()
            url = 'https://res.weread.qq.com/wrepub/test_cover.jpg'
            (image_cache / hashlib.md5(url.encode()).hexdigest()).write_bytes(PNG)
            css_href = '../' + style_path if chapter_path.startswith('Text/') else style_path
            cover = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title><link href="' + css_href + '" rel="stylesheet"/></head><body><img src="' + url + '"/></body></html>'
            (book / chapter_path).write_text(cover)
            style = book / style_path
            style.write_text('body {color:black}')
            with sqlite3.connect(data / 'Documents/WeReadDB_123.db') as db:
                db.execute('create table WRBook(bookId TEXT,title TEXT,author TEXT,isbn TEXT,publisher TEXT,chapterSize INTEGER,format TEXT)')
                db.execute('create table WRChapter(bookId TEXT,chapterIdx INTEGER,title TEXT,indentLevel INTEGER,relativePath TEXT,relativePaths BLOB)')
                db.execute("insert into WRBook values('1','Root book','Author','','',1,'epub')")
                db.execute("insert into WRChapter values('1',1,'Cover',1,?,NULL)", (chapter_path,))
            with patch.object(worker, 'DATA', data):
                item = worker.discover()[0]
                self.assertTrue(item['ready'], item['status'])
                self.assertEqual((item['downloaded'], item['expected']), (1, 1))
                style.write_text('body {color:blue}')
                self.assertNotEqual(item['fingerprint'], worker.discover()[0]['fingerprint'])
                result = worker.export_one('123:1', tmp / 'output', tmp / 'state.json', tmp / 'cache', tmp / 'seed')['result']
                self.assertTrue(worker.annotate(worker.discover(), tmp / 'output', worker.state_load(tmp / 'state.json'))[0]['exported'])
            with zipfile.ZipFile(result['epub']) as archive:
                self.assertEqual(archive.read('OEBPS/' + style_path), b'body {color:blue}')
                doc = ET.fromstring(archive.read('OEBPS/' + chapter_path))
                src = doc.find('.//{' + worker.core.XHTML + '}img').get('src')
                image_path = 'Images/' + src.rsplit('/', 1)[-1]
                self.assertEqual(archive.read('OEBPS/' + image_path), PNG)
                opf = ET.fromstring(archive.read('OEBPS/content.opf'))
                cover_item = next(e for e in opf.iter() if e.get('properties') == 'cover-image')
                self.assertEqual(cover_item.get('href'), image_path)
            self.assertEqual((book / chapter_path).read_text(), cover)

    def test_paths_reject_escape_and_remote_sources(self):
        for path in ('../cover.xhtml', '%2e%2e/cover.xhtml', '/cover.xhtml', 'https://example.com/cover.xhtml', 'Text/../../cover.xhtml'):
            with self.subTest(path=path), self.assertRaises(worker.ExportError):
                worker.read_paths(None, path)
        self.assertEqual(worker.read_paths(None, 'Text/chapter.xhtml#part'), ['Text/chapter.xhtml#part'])
        self.assertEqual(worker.read_paths(None, 'cover.xhtml'), ['cover.xhtml'])

if __name__ == '__main__':
    unittest.main()
