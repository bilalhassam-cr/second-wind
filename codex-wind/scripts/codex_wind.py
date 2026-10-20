#!/usr/bin/env python3
"""Compatibility command; install and use Second Wind in either harness."""
import runpy
from pathlib import Path
import sys
if len(sys.argv) > 1 and sys.argv[1] == 'import-second-wind':
    sys.argv[1] = 'migrate-codex-wind'
runpy.run_path(str(Path(__file__).resolve().parents[2] / 'second-wind/scripts/second_wind.py'), run_name='__main__')
