"""Real worker processes must overlap without losing state or overwriting EPUBs."""
from contextlib import closing, redirect_stdout
import io
import base64
import hashlib
import shutil
import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import zipfile
import worker


def export_process(data, root, bid, barrier, results):
    worker.DATA = Path(data)
    original = worker.core.export_book
    def synchronized_export(*args, **kwargs):
        if barrier is not None:
            barrier.wait(timeout=3)
        else:
            time.sleep(0.2)
        return original(*args, **kwargs)
    worker.core.export_book = synchronized_export
    sys.argv = ['worker.py', 'export', '--id', bid, '--output', root + '/output',
                '--state', root + '/state.json', '--cache', root + '/cache', '--seed', root + '/seed']
    output = io.StringIO()
    with redirect_stdout(output):
        worker.main()
    results.put(json.loads(output.getvalue()))


class ParallelExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'Data'
        (self.data / 'Documents').mkdir(parents=True)
        self.image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        url = 'https://res.weread.qq.com/wrepub/parallel_test'
        digest = hashlib.md5(url.encode()).hexdigest()
        with closing(sqlite3.connect(self.data / 'Documents/WeReadDB_123.db')) as db:
            db.execute('create table WRBook(bookId TEXT,title TEXT,author TEXT,isbn TEXT,publisher TEXT,chapterSize INTEGER,format TEXT)')
            db.execute('create table WRChapter(bookId TEXT,chapterIdx INTEGER,title TEXT,indentLevel INTEGER,relativePath TEXT,relativePaths BLOB)')
            for bid in ('1', '2', '3'):
                folder = self.data / ('Library/123/Books/' + bid + '/Text')
                folder.mkdir(parents=True)
                seed = self.root / 'seed' / bid; seed.mkdir(parents=True)
                (seed / digest).write_bytes(self.image)
                (folder / 'ch.xhtml').write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head><body><p>Book ' + bid + '</p><img src="' + url + '"/></body></html>')
                db.execute("insert into WRBook values(?,'Same title','Author','','',1,'epub')", (bid,))
                db.execute("insert into WRChapter values(?,1,'Chapter',1,'Text/ch.xhtml',NULL)", (bid,))
            db.commit()

    def run_exports(self, bids, synchronize):
        ctx = multiprocessing.get_context('spawn')
        results = ctx.Queue()
        barrier = ctx.Barrier(len(bids)) if synchronize else None
        processes = [ctx.Process(target=export_process, args=(str(self.data), str(self.root), bid, barrier, results)) for bid in bids]
        for process in processes: process.start()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive(): process.terminate(); process.join()
            self.assertEqual(process.exitcode, 0)
        outputs = [results.get(timeout=3) for _ in processes]
        results.close(); results.join_thread()
        self.assertTrue(all(r['ok'] for r in outputs), outputs)
        return outputs

    def test_three_exports_overlap_preserve_state_and_same_titles(self):
        outputs = self.run_exports(['123:1', '123:2', '123:3'], synchronize=True)
        state = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(len(state), 3)
        files = list((self.root / 'output').glob('*.epub'))
        self.assertEqual(len(files), 3)
        self.assertEqual(len({r['result']['epub'] for r in outputs}), 3)
        for result in outputs:
            with zipfile.ZipFile(result['result']['epub']) as archive:
                self.assertIsNone(archive.testzip())
                images = [n for n in archive.namelist() if n.startswith('OEBPS/Images/')]
                self.assertEqual(len(images), 1)
                self.assertEqual(archive.read(images[0]), self.image)
                self.assertIn(('Book ' + result['result']['book_id']).encode(), archive.read('OEBPS/Text/ch.xhtml'))

    def test_same_book_id_in_different_accounts_keeps_both_outputs(self):
        shutil.copytree(self.data / 'Library/123', self.data / 'Library/456')
        shutil.copy2(self.data / 'Documents/WeReadDB_123.db', self.data / 'Documents/WeReadDB_456.db')
        (self.data / 'Library/456/Books/1/Text/ch.xhtml').write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Test</title></head><body>Other account</body></html>')
        outputs = self.run_exports(['123:1', '456:1'], synchronize=True)
        self.assertEqual(len({r['result']['epub'] for r in outputs}), 2)
        texts = []
        for output in outputs:
            with zipfile.ZipFile(output['result']['epub']) as archive:
                texts.append(archive.read('OEBPS/Text/ch.xhtml'))
        self.assertTrue(any(b'Book 1' in text for text in texts))
        self.assertTrue(any(b'Other account' in text for text in texts))

    def test_duplicate_book_is_exported_only_once(self):
        outputs = self.run_exports(['123:1', '123:1'], synchronize=False)
        self.assertEqual(sum(r['skipped'] for r in outputs), 1)
        self.assertEqual(len(list((self.root / 'output').glob('*.epub'))), 1)

if __name__ == '__main__':
    unittest.main()
