"""Verified export history remains visible when source cache directories disappear."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import worker

class ExportHistoryTest(unittest.TestCase):
    def test_export_survives_missing_cache_but_not_missing_epub(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            epub = output / 'Book.epub'; epub.write_bytes(b'exported file')
            saved = {'book_id': '7', 'title': 'Book', 'author': 'Author', 'epub': str(epub), 'fingerprint': 'old', 'cached_xhtml_files': 30}
            state = {'123:7|' + str(output): saved, '123:8|/other/output': saved}
            with patch.object(worker, 'discover', return_value=[]):
                books = worker.scan_books(output, state)
                self.assertEqual(len(books), 1)
                self.assertTrue(books[0]['exported'])
                self.assertFalse(books[0]['ready'])
                self.assertFalse(books[0]['source_cached'])
                self.assertEqual(books[0]['id'], '123:7')
                epub.unlink()
                self.assertEqual(worker.scan_books(output, state), [])

    def test_current_cache_is_not_duplicated_or_overridden_by_old_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp); epub = output / 'Book.epub'; epub.write_bytes(b'exported file')
            saved = {'title': 'Book', 'author': '', 'epub': str(epub), 'fingerprint': 'old'}
            current = {'id': '123:7', 'title': 'Book', 'fingerprint': 'new', 'ready': True}
            with patch.object(worker, 'discover', return_value=[current]):
                books = worker.scan_books(output, {'123:7|' + str(output): saved})
                self.assertEqual(len(books), 1)
                self.assertFalse(books[0]['exported'])
                self.assertTrue(books[0]['source_cached'])

if __name__ == '__main__': unittest.main()
