#!/usr/bin/env python3
"""Run synthetic Python regressions and Swift UI-model tests on macOS."""
from pathlib import Path
import os
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1')
for test in sorted((ROOT / 'tests').glob('test_*.py')):
    subprocess.run([sys.executable, '-B', str(test)], env=env, check=True)
source = (ROOT / 'src/App.swift').read_text()
with tempfile.TemporaryDirectory(prefix='weread-tests-') as temp:
    path = Path(temp)
    swift = path / 'LibrarySource.swift'
    swift.write_text(source[:source.index('struct ContentView: View')])
    binary = path / 'LibraryTests'
    subprocess.run(['xcrun', 'swiftc', '-D', 'DEBUG', '-parse-as-library', '-target',
        platform.machine() + '-apple-macos13.0', str(swift),
        str(ROOT / 'tests/test_library.swift'), '-o', str(binary)], check=True)
    subprocess.run([str(binary), '--ui-fixture'], check=True)
