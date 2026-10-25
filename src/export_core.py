#!/usr/bin/env python3
"""Export downloaded WeRead EPUB cache files on macOS; source files stay read-only."""
import argparse
import base64
import ctypes as C
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

BOOKS = {}
XHTML = 'http://www.w3.org/1999/xhtml'
OPF = 'http://www.idpf.org/2007/opf'
DC = 'http://purl.org/dc/elements/1.1/'
EPUB = 'http://www.idpf.org/2007/ops'
NCX = 'http://www.daisy.org/z3986/2005/ncx/'
ET.register_namespace('', XHTML)
ET.register_namespace('epub', EPUB)
ET.register_namespace('dc', DC)

_crypto = C.CDLL('/usr/lib/system/libcommonCrypto.dylib')
_crypto.CCCrypt.argtypes = [C.c_uint, C.c_uint, C.c_uint, C.c_void_p,
    C.c_size_t, C.c_void_p, C.c_void_p, C.c_size_t, C.c_void_p,
    C.c_size_t, C.POINTER(C.c_size_t)]
_crypto.CCCrypt.restype = C.c_int

def aes_cbc(cipher, key, iv=None):
    if len(key) not in (16, 24, 32) or len(cipher) % 16:
        raise ValueError('Invalid AES input lengths')
    secret = C.create_string_buffer(key)
    source = C.create_string_buffer(cipher)
    ivbuf = C.create_string_buffer(iv) if iv is not None else None
    target = C.create_string_buffer(len(cipher) + 16)
    written = C.c_size_t()
    status = _crypto.CCCrypt(1, 0, 1, secret, len(key), ivbuf, source,
                             len(cipher), target, len(target), C.byref(written))
    if status:
        raise ValueError('AES decryption failed; account/cache may not match')
    return target.raw[:written.value]

def decode_file(blob, account):
    if not blob.startswith(b'WR721KEY'):
        return blob
    if len(blob) <= 32:
        raise ValueError('Truncated encrypted file')
    wrapped = base64.b64decode(blob[8:32], validate=True)
    seed = account.encode('ascii')
    if not seed:
        raise ValueError('Empty account identifier')
    material = (seed * ((32 + len(seed) - 1) // len(seed)))[:32]
    password = aes_cbc(wrapped, material[:16], material[16:32])
    if not password:
        raise ValueError('Empty book password')
    size = 32 if len(password) >= 32 else 24 if len(password) >= 24 else 16
    key = (password * ((size + len(password) - 1) // len(password)))[:size]
    return aes_cbc(blob[32:], key)

def image_type(blob):
    if blob.startswith(b'\xff\xd8'):
        return 'image/jpeg', '.jpg'
    if blob.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if blob.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif', '.gif'
    if blob.startswith(b'RIFF') and blob[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    raise ValueError('Unknown image format')

def xml(blob):
    return ET.tostring(blob, encoding='utf-8', xml_declaration=True)

def parse_xhtml(blob):
    """Normalize XHTML nonbreaking spaces and identical repeated data-* attributes."""
    text = blob.decode('utf-8')
    # XHTML's named nonbreaking space is not built into the XML parser.
    text = re.sub(r'<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|&nbsp;',
                  lambda match: '&#160;' if match[0] == '&nbsp;' else match[0], text)
    try:
        return ET.fromstring(text), 0
    except ET.ParseError as original_error:
        if 'duplicate attribute' not in str(original_error):
            raise
        line_offsets = [0]
        for line in text.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1] + len(line))
        removals = []

        class DuplicateScanner(HTMLParser):
            def handle_starttag(self, tag, attrs):
                raw = self.get_starttag_text()
                start = re.match(r'<[A-Za-z_:][\w:.-]*', raw)
                if start is None:
                    return
                position = start.end()
                seen = {}
                line, column = self.getpos()
                offset = line_offsets[line - 1] + column
                pattern = re.compile(r'''\s+([A-Za-z_:][\w:.-]*)\s*=\s*("([^"]*)"|'([^']*)')''')
                while True:
                    match = pattern.match(raw, position)
                    if match is None:
                        break
                    name = match.group(1)
                    value = match.group(3) if match.group(3) is not None else match.group(4)
                    if name in seen and name.startswith('data-'):
                        if seen[name] != value:
                            raise ValueError('重复图片属性的值不一致：' + name)
                        removals.append((offset + match.start(), offset + match.end()))
                    else:
                        seen[name] = value
                    position = match.end()

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)

        scanner = DuplicateScanner(convert_charrefs=False)
        scanner.feed(text)
        scanner.close()
        if not removals:
            raise original_error
        for start, end in sorted(removals, reverse=True):
            text = text[:start] + text[end:]
        return ET.fromstring(text), len(removals)

def find_books_root():
    library = Path.home() / 'Library/Containers/com.tencent.weread/Data/Library'
    matches = [p for p in library.glob('*/Books')
               if p.parent.name.isdigit() and any((p / i).is_dir() for i in BOOKS)]
    if len(matches) != 1:
        raise ValueError('Specify --books-root; could not identify one matching account')
    return matches[0]

def chapter_files(book):
    return {str(path.relative_to(book)) for folder in (book, book / 'Text')
            for path in folder.glob('*')
            if path.is_file() and path.suffix in ('.xhtml', '.html')}

def export_book(book_id, root, output, cache, fetch_missing=False):
    metadata = BOOKS[book_id]
    book = root / book_id
    account = root.parent.name
    hashes = {}
    resources = {}
    media = {}
    remote_images = {}
    misses = []
    docs = {}
    normalized_attributes = 0

    def read_source(path):
        if not path.is_relative_to(book):
            raise ValueError('Source path outside the selected book')
        blob = path.read_bytes()
        hashes[path] = hashlib.sha256(blob).hexdigest()
        return blob

    expected = [p for ch in metadata['chapters'] for p in ch['paths']]
    expected_files = {urllib.parse.unquote(p.split('#', 1)[0]) for p in expected}
    is_txt = metadata.get('format') == 'txt'
    actual = expected_files if is_txt else chapter_files(book)
    if expected_files != actual:
        raise ValueError('Chapter metadata and cache file set do not match')
    txt_chapters = {ch['paths'][0]: ch for ch in metadata['chapters']} if is_txt else {}

    for relative in sorted(actual):
        if is_txt:
            chapter = txt_chapters[relative]
            source = chapter['source']
            if not re.fullmatch(re.escape(book_id) + r'_\d+_o', source):
                raise ValueError('Invalid TXT chapter source')
            text = decode_file(read_source(book / source), account).decode('utf-8-sig')
            if not text.strip():
                raise ValueError('Empty TXT chapter: ' + source)
            doc = ET.Element('{' + XHTML + '}html', {'lang': 'zh-CN'})
            head = ET.SubElement(doc, '{' + XHTML + '}head')
            ET.SubElement(head, '{' + XHTML + '}title').text = chapter['title']
            body = ET.SubElement(doc, '{' + XHTML + '}body')
            ET.SubElement(body, '{' + XHTML + '}h1').text = chapter['title']
            ET.SubElement(body, '{' + XHTML + '}pre', {
                'style': 'white-space: pre-wrap; font-family: inherit;'
            }).text = text.replace('\r\n', '\n').replace('\r', '\n')
            normalized = 0
        else:
            plain = decode_file(read_source(book / relative), account)
            doc, normalized = parse_xhtml(plain)
        normalized_attributes += normalized
        if doc.tag != '{' + XHTML + '}html':
            raise ValueError('Invalid XHTML document: ' + relative)
        title = doc.find('{' + XHTML + '}head/{' + XHTML + '}title')
        if title is not None and not (title.text or '').strip():
            title.text = next(ch['title'] for ch in metadata['chapters']
                              if relative in [p.split('#', 1)[0] for p in ch['paths']])
        docs[relative] = doc
    for path in sorted(list((book / 'Styles').glob('*.css')) + list((book / 'css').glob('*.css'))):
        plain = decode_file(read_source(path), account)
        plain.decode('utf-8')
        name = str(path.relative_to(book))
        resources[name], media[name] = plain, 'text/css'
    image_cache = book / 'com.hackemist.SDWebImageCache.cachedImages'
    cached = {p.name[:32]: p for p in image_cache.glob('*') if p.is_file()}

    def resolve_image(url, name=None):
        if url in remote_images:
            return remote_images[url]
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'res.weread.qq.com':
            raise ValueError('Unexpected image source')
        digest = hashlib.md5(url.encode('utf-8')).hexdigest()
        local = cached.get(digest)
        cache_path = cache / book_id / digest
        if local:
            blob = read_source(local)
        elif cache_path.exists():
            blob = cache_path.read_bytes()
        elif fetch_missing:
            clean = re.sub(r'&x+$', '', url)
            response = subprocess.run(['/usr/bin/curl', '--fail', '--location',
                '--silent', '--show-error', '--connect-timeout', '8', '--max-time',
                '30', clean], capture_output=True, check=True)
            blob = response.stdout
            image_type(blob)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(blob)
        else:
            misses.append(url)
            return None
        mime, suffix = image_type(blob)
        name = name or 'Images/' + digest + suffix
        resources[name], media[name] = blob, mime
        remote_images[url] = name
        return name

    for path in sorted((book / 'Images').glob('*')):
        if not path.is_file():
            continue
        plain = decode_file(read_source(path), account)
        name = 'Images/' + path.name
        # Some cache entries contain a CDN URL instead of image bytes.
        if plain.startswith(b'https://'):
            resolved = resolve_image(plain.decode('utf-8').strip(), name)
            if resolved:
                resources[name], media[name] = resources[resolved], media[resolved]
        else:
            mime, _ = image_type(plain)
            resources[name], media[name] = plain, mime

    # Preserve the original document structure and text; replace image URLs only.
    for relative, doc in docs.items():
        for node in doc.iter():
            if node.tag.rsplit('}', 1)[-1] == 'img':
                src = node.get('src', '')
                if src.startswith('https://'):
                    name = resolve_image(src)
                    if name:
                        node.set('src', posixpath.relpath(name, posixpath.dirname(relative) or '.'))
        resources[relative], media[relative] = xml(doc), 'application/xhtml+xml'
    if misses:
        raise ValueError(str(len(set(misses))) + ' images absent from local cache; '
                         'use --fetch-missing or provide --image-cache')

    # CSS already falls back to list-style-type when a marker image is absent.
    # Make that fallback explicit so missing decoration cannot block the book.
    normalized_missing_list_images = 0
    for stylesheet_name in list(resources):
        if not stylesheet_name.endswith('.css'):
            continue

        def list_marker_fallback(match):
            nonlocal normalized_missing_list_images
            if not match.group('property'):
                return match[0]
            parsed = urllib.parse.urlsplit(match.group('link'))
            target = posixpath.normpath(posixpath.join(posixpath.dirname(stylesheet_name),
                                                       urllib.parse.unquote(parsed.path)))
            if parsed.scheme or parsed.netloc or not parsed.path or target in resources:
                return match[0]
            normalized_missing_list_images += 1
            return match.group('property') + 'none'

        css = resources[stylesheet_name].decode('utf-8')
        css = re.sub(r"""/\*[\s\S]*?\*/|(?P<property>(?<![\w-])list-style-image\s*:\s*)url\(\s*["']?(?P<link>[^)"']+?)["']?\s*\)""",
                     list_marker_fallback, css, flags=re.IGNORECASE)
        resources[stylesheet_name] = css.encode('utf-8')

    nav = ET.Element('{' + XHTML + '}html', {'lang': 'zh-CN'})
    head = ET.SubElement(nav, '{' + XHTML + '}head')
    ET.SubElement(head, '{' + XHTML + '}title').text = metadata['title'] + '目录'
    body = ET.SubElement(nav, '{' + XHTML + '}body')
    toc = ET.SubElement(body, '{' + XHTML + '}nav', {'{' + EPUB + '}type': 'toc', 'id': 'toc'})
    ET.SubElement(toc, '{' + XHTML + '}h1').text = '目录'
    top = ET.SubElement(toc, '{' + XHTML + '}ol')
    parent_item = None
    nested = None
    for chapter in metadata['chapters']:
        if chapter['level'] > 1 and parent_item is not None:
            if nested is None:
                nested = ET.SubElement(parent_item, '{' + XHTML + '}ol')
            item = ET.SubElement(nested, '{' + XHTML + '}li')
        else:
            item = ET.SubElement(top, '{' + XHTML + '}li')
            parent_item, nested = item, None
        ET.SubElement(item, '{' + XHTML + '}a', {'href': chapter['paths'][0]}).text = chapter['title']
    resources['nav.xhtml'], media['nav.xhtml'] = xml(nav), 'application/xhtml+xml'

    uid = 'urn:weread-cache:' + book_id
    ncx = ET.Element('{' + NCX + '}ncx', {'version': '2005-1'})
    nh = ET.SubElement(ncx, '{' + NCX + '}head')
    ET.SubElement(nh, '{' + NCX + '}meta', {'name': 'dtb:uid', 'content': uid})
    ET.SubElement(nh, '{' + NCX + '}meta', {'name': 'dtb:depth', 'content': '1'})
    dt = ET.SubElement(ncx, '{' + NCX + '}docTitle')
    ET.SubElement(dt, '{' + NCX + '}text').text = metadata['title']
    nm = ET.SubElement(ncx, '{' + NCX + '}navMap')
    for index, chapter in enumerate(metadata['chapters'], 1):
        point = ET.SubElement(nm, '{' + NCX + '}navPoint', {'id': 'n'+str(index), 'playOrder': str(index)})
        label = ET.SubElement(point, '{' + NCX + '}navLabel')
        ET.SubElement(label, '{' + NCX + '}text').text = chapter['title']
        ET.SubElement(point, '{' + NCX + '}content', {'src': chapter['paths'][0]})
    resources['toc.ncx'], media['toc.ncx'] = xml(ncx), 'application/x-dtbncx+xml'

    cover_path = next((name for name in ('Text/cover.xhtml', 'cover.xhtml',
                       'Text/coverpage.html', 'coverpage.html', 'Text/cover.html', 'cover.html')
                       if name in docs), None)
    cover_doc = docs.get(cover_path)
    cover_image = None
    if cover_doc is not None:
        cover_node = next((e for e in cover_doc.iter() if e.tag.rsplit('}', 1)[-1] == 'img'), None)
        if cover_node is not None:
            cover_image = posixpath.normpath(posixpath.join(posixpath.dirname(cover_path), cover_node.get('src', '')))
    package = ET.Element('{' + OPF + '}package', {'version': '3.0', 'unique-identifier': 'book-id'})
    md = ET.SubElement(package, '{' + OPF + '}metadata')
    ET.SubElement(md, '{' + DC + '}identifier', {'id': 'book-id'}).text = uid
    ET.SubElement(md, '{' + DC + '}title').text = metadata['title']
    ET.SubElement(md, '{' + DC + '}creator').text = metadata['author']
    ET.SubElement(md, '{' + DC + '}language').text = 'zh-CN'
    if metadata['publisher']:
        ET.SubElement(md, '{' + DC + '}publisher').text = metadata['publisher']
    ET.SubElement(md, '{' + OPF + '}meta', {'property': 'dcterms:modified'}).text = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    manifest = ET.SubElement(package, '{' + OPF + '}manifest')
    ids = {}
    for i, name in enumerate(resources):
        item_id = 'resource-' + str(i)
        ids[name] = item_id
        attrs = {'id': item_id, 'href': name, 'media-type': media[name]}
        if name == 'nav.xhtml':
            attrs['properties'] = 'nav'
        elif name == cover_image:
            attrs['properties'] = 'cover-image'
        ET.SubElement(manifest, '{' + OPF + '}item', attrs)
    spine = ET.SubElement(package, '{' + OPF + '}spine', {'toc': ids['toc.ncx']})
    for relative in expected:
        ET.SubElement(spine, '{' + OPF + '}itemref', {'idref': ids[relative.split('#',1)[0]]})
    resources['content.opf'] = xml(package)
    container = ET.Element('container', {'version': '1.0', 'xmlns': 'urn:oasis:names:tc:opendocument:xmlns:container'})
    rootfiles = ET.SubElement(container, 'rootfiles')
    ET.SubElement(rootfiles, 'rootfile', {'full-path': 'OEBPS/content.opf', 'media-type': 'application/oebps-package+xml'})

    output.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[/:\\\x00-\x1f]', '_', metadata['title']).strip(' .') or book_id
    filename = output / (safe_title + '.epub')
    if filename.exists():
        filename = output / (safe_title + '（' + book_id + '）.epub')
    if filename.exists():
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        filename = output / (safe_title + '（' + book_id + '，' + stamp + '）.epub')
        number = 2
        while filename.exists():
            filename = output / (safe_title + '（' + book_id + '，' + stamp + '-' + str(number) + '）.epub')
            number += 1
    temporary = output / ('.' + book_id + '.epub.tmp')
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', xml(container))
        for name, blob in resources.items():
            archive.writestr('OEBPS/' + name, blob)
    # Structural and local resource checks before publishing the result.
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt EPUB ZIP')
        names = set(archive.namelist())
        for name, blob in resources.items():
            if name.endswith(('.xhtml', '.html', '.opf', '.ncx')):
                document = ET.fromstring(blob)
                for node in document.iter():
                    for attribute in ('src', 'href'):
                        link = node.get(attribute, '')
                        parsed = urllib.parse.urlsplit(link)
                        if not link or parsed.scheme or not parsed.path:
                            continue
                        target = posixpath.normpath(posixpath.join('OEBPS', posixpath.dirname(name), urllib.parse.unquote(parsed.path)))
                        if target not in names:
                            raise ValueError('Missing resource: ' + target)
        for name, blob in resources.items():
            if name.endswith('.css'):
                for link in re.findall(r'url\([\"\']?([^\)\"\']+)', blob.decode('utf-8')):
                    parsed = urllib.parse.urlsplit(link)
                    if parsed.scheme or not parsed.path:
                        raise ValueError('Unsupported stylesheet image source')
                    target = posixpath.normpath(posixpath.join('OEBPS', posixpath.dirname(name), urllib.parse.unquote(parsed.path)))
                    if target not in names:
                        raise ValueError('Missing stylesheet resource: ' + target)
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != digest for p, digest in hashes.items()):
        raise ValueError('A source file changed during export')
    temporary.replace(filename)
    body_characters = 0
    for doc in docs.values():
        for node in doc.iter():
            if node.tag == '{' + XHTML + '}body':
                body_characters += len(''.join(node.itertext()).strip())
    return {'book_id': book_id, 'title': metadata['title'], 'author': metadata['author'],
            'epub': str(filename.resolve()), 'cached_xhtml_files': len(docs),
            'toc_entries': len(metadata['chapters']), 'body_characters': body_characters,
            'embedded_images': sum(t.startswith('image/') for t in media.values()),
            'missing_images': 0, 'source_files_verified_unchanged': len(hashes),
            'normalized_duplicate_attributes': normalized_attributes,
            'normalized_missing_list_images': normalized_missing_list_images,
            'epub_sha256': hashlib.sha256(filename.read_bytes()).hexdigest()}
