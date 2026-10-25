#!/usr/bin/env python3
"""Build a local macOS app using an explicitly supplied standalone Python runtime."""
import argparse
import json
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--python-runtime', type=Path, required=True,
                    help='Standalone Python prefix containing bin/ and lib/python3.x/')
parser.add_argument('--output', type=Path, default=ROOT / 'dist')
args = parser.parse_args()
runtime = args.python_runtime.resolve()
python = next((p for p in (runtime / 'bin').glob('python3*') if p.is_file() and not p.name.endswith('-config')), None)
if python is None:
    parser.error('Runtime must contain bin/python3 or bin/python3.x')
info = json.loads(subprocess.check_output([str(python), '-I', '-c',
    'import json,sys;print(json.dumps({"version":f"python{sys.version_info.major}.{sys.version_info.minor}"}))'], text=True))
stdlib = runtime / 'lib' / info['version']
if not stdlib.is_dir():
    parser.error('Runtime is missing ' + str(stdlib))
app = args.output.resolve() / '微信读书导出助手.app'
if app.exists():
    parser.error('Output app already exists; choose another --output directory')
resources = app / 'Contents/Resources'
macos = app / 'Contents/MacOS'
resources.mkdir(parents=True)
macos.mkdir(parents=True)
subprocess.run(['xcrun', 'swiftc', '-O', '-parse-as-library', '-target',
    platform.machine() + '-apple-macos13.0', str(ROOT / 'src/App.swift'),
    '-o', str(macos / 'WeReadExporter')], check=True)
for name in ('worker.py', 'export_core.py'):
    shutil.copy2(ROOT / 'src' / name, resources / name)
shutil.copy2(ROOT / 'AppIcon.icns', resources / 'AppIcon.icns')
# Preserve runtime libraries and license files; omit unrelated installed packages.
shutil.copytree(runtime, resources / 'Python', symlinks=True,
    ignore=shutil.ignore_patterns('site-packages', '__pycache__', '*.pyc'))
bundled = resources / 'Python/bin/python3'
if not bundled.exists():
    bundled.symlink_to(python.name)
subprocess.run([str(bundled), '-I', '-B', '-c',
    'import ctypes,sqlite3,ssl,xml.etree.ElementTree;print("Bundled Python ready")'],
    cwd=app, check=True)
info = {'CFBundleName': '微信读书导出助手', 'CFBundleDisplayName': '微信读书导出助手',
    'CFBundleExecutable': 'WeReadExporter', 'CFBundleIdentifier': 'io.github.alexkwok213.WeReadExporter',
    'CFBundleVersion': '9', 'CFBundleShortVersionString': '2.4.3',
    'CFBundleIconFile': 'AppIcon.icns', 'CFBundlePackageType': 'APPL',
    'LSMinimumSystemVersion': '13.0', 'NSHighResolutionCapable': True,
    'NSPrincipalClass': 'NSApplication',
    'NSDocumentsFolderUsageDescription': '读取您已下载的微信读书内容以导出 EPUB。',
    'NSDownloadsFolderUsageDescription': '将导出的 EPUB 保存在您选择的目录中。'}
with (app / 'Contents/Info.plist').open('wb') as handle:
    plistlib.dump(info, handle)
subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
print(app)
