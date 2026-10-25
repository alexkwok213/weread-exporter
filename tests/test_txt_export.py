"""TXT caches use chapter UIDs for files and chapter indexes for reading order."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
import worker

class TxtExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data = self.root / 'Data'
        self.book = data / 'Library/123/Books/7'
        self.book.mkdir(parents=True)
        (data / 'Documents').mkdir()
        with sqlite3.connect(data / 'Documents/WeReadDB_123.db') as db:
            db.execute('create table WRBook(bookId TEXT,title TEXT,author TEXT,isbn TEXT,publisher TEXT,chapterSize INTEGER,format TEXT)')
            db.execute('create table WRChapter(bookId TEXT,chapterIdx INTEGER,title TEXT,indentLevel INTEGER,relativePath TEXT,relativePaths BLOB,chapterUid INTEGER)')
            db.execute("insert into WRBook values('7','TXT Test','Author','','',3,'txt')")
            for index, uid in [(1, 10), (2, 2), (3, 35)]:
                db.execute("insert into WRChapter values('7',?,?,1,NULL,NULL,?)", (index, 'Chapter ' + str(index), uid))
        db.close()
        self.texts = ['First <tag> & text\r\n  New paragraph\r\n\r\nEnd', 'Second 正文', 'Third 正文']
        for uid, text in zip([10, 2, 35], self.texts):
            (self.book / ('7_' + str(uid) + '_o')).write_bytes(text.encode())
        patcher = patch.object(worker, 'DATA', data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def export(self):
        return worker.export_one('123:7', self.root / 'output', self.root / 'state.json', self.root / 'cache', self.root / 'seed')

    def test_txt_discovers_and_exports_in_catalog_order(self):
        item = worker.discover()[0]
        self.assertTrue(item['ready'], item['status'])
        self.assertEqual((item['downloaded'], item['expected']), (3, 3))
        result = self.export()['result']
        with zipfile.ZipFile(result['epub']) as archive:
            opf = ET.fromstring(archive.read('OEBPS/content.opf'))
            ns = {'o': worker.core.OPF}
            manifest = {e.get('id'): e.get('href') for e in opf.findall('o:manifest/o:item', ns)}
            paths = [manifest[e.get('idref')] for e in opf.findall('o:spine/o:itemref', ns)]
            self.assertEqual(len(paths), 3)
            for index, (path, text) in enumerate(zip(paths, self.texts), 1):
                doc = ET.fromstring(archive.read('OEBPS/' + path))
                self.assertEqual(doc.find('.//{' + worker.core.XHTML + '}h1').text, 'Chapter ' + str(index))
                self.assertEqual(doc.find('.//{' + worker.core.XHTML + '}pre').text, text.replace('\r\n', '\n'))
        self.assertEqual((self.book / '7_10_o').read_bytes(), self.texts[0].encode())
        self.assertTrue(self.export()['skipped'])

    def test_missing_chapter_is_not_ready(self):
        (self.book / '7_2_o').unlink()
        item = worker.discover()[0]
        self.assertFalse(item['ready'])
        self.assertEqual((item['downloaded'], item['expected']), (2, 3))
        with self.assertRaises(worker.ExportError): self.export()

    def test_corrupt_or_empty_chapter_cannot_export(self):
        for blob in (b'WR721KEYtruncated', b'\xff\xfe\x00', b'   '):
            with self.subTest(blob=blob):
                (self.book / '7_2_o').write_bytes(blob)
                with self.assertRaises((ValueError, UnicodeDecodeError)): self.export()
        self.assertFalse(list((self.root / 'output').glob('*.epub')))

if __name__ == '__main__':
    unittest.main()
