import sys
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).parent));import export_core as core
sample=b'''<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Before &amp; after</p><img src="x.png" data-w="720px" data-ratio="0.606" data-w="720px" data-ratio="0.606"/><!-- <img data-w="1" data-w="1"/> --><p><![CDATA[<img data-w="2" data-w="2"/>]]></p></body></html>'''
doc,count=core.parse_xhtml(sample)
assert count==2
assert ''.join(doc.itertext())=='Before & after<img data-w="2" data-w="2"/>'
image=doc.find('.//{'+core.XHTML+'}img');assert image.attrib=={'src':'x.png','data-w':'720px','data-ratio':'0.606'}
clean=b'<html><body><p>Valid source</p></body></html>'
assert core.parse_xhtml(clean)[1]==0
for invalid in [b'<html><img data-w="1" data-w="2"/></html>',b'<html><img src="a" src="b"/></html>',b'<html><p>Truncated']:
 try:core.parse_xhtml(invalid)
 except (ValueError,ET.ParseError):pass
 else:raise AssertionError('Conflicting or malformed source must be rejected')
print('PASS: identical data attributes removed; body text, CDATA and image attributes preserved; conflicting attributes and malformed XML rejected')
