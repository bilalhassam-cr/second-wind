#!/usr/bin/env python3
"""Compatibility command for the former Codex Wind runner."""
import runpy
from pathlib import Path
import sys
if len(sys.argv) > 1 and sys.argv[1] == 'import-second-wind':
    sys.argv[1] = 'migrate-codex-wind'
runpy.run_path(str(Path(__file__).with_name('second_wind.py')), run_name='__main__')
