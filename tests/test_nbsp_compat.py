"""XHTML nonbreaking spaces must survive without altering comments or CDATA."""
import unittest
import xml.etree.ElementTree as ET
import export_core as core

class NonbreakingSpaceTest(unittest.TestCase):
    def test_nbsp_and_duplicate_attributes_preserve_content(self):
        doc, count = core.parse_xhtml(b'<html><body><p>A&nbsp;B &amp; C</p><img data-w="1" data-w="1"/><p><![CDATA[&nbsp;]]></p><!-- &nbsp; --></body></html>')
        self.assertEqual(''.join(doc.itertext()), 'A\u00a0B & C&nbsp;')
        self.assertEqual(count, 1)

    def test_html_named_entities_preserve_spacing_symbols_and_markup_text(self):
        doc, count = core.parse_xhtml(b'<html><p>&ensp;A&emsp;B&thinsp;&copy;&mdash;&lt;tag&gt;&amp;</p><p><![CDATA[&ensp;]]></p></html>')
        self.assertEqual(''.join(doc.itertext()), '\u2002A\u2003B\u2009\u00a9\u2014<tag>&&ensp;')
        self.assertEqual(count, 0)

    def test_unknown_entities_are_still_rejected(self):
        with self.assertRaises(ET.ParseError):
            core.parse_xhtml(b'<html><p>&unknown;</p></html>')

if __name__ == '__main__':
    unittest.main()
