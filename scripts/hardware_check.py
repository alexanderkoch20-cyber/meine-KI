#!/usr/bin/env python3
"""Duenner Wrapper: `python scripts/hardware_check.py`"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.diagnostics import format_report, run_hardware_check

if __name__ == "__main__":
    print(format_report(run_hardware_check()))
