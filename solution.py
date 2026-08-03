#!/usr/bin/env python3
"""Entry point — dispatches to the active version's pipeline.

Active pipeline: v4 (standalone; imports nothing from v1/v2/v3).
v1-v3 remain in the repository as frozen reference — see
docs/superpowers/specs/2026-08-03-v4-standalone-rewrite-design.md.
"""
import sys

from v4.solution import main, VERSION  # active pipeline


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: solution.py <input_pdf_dir> <output_path>  (version={VERSION})")
    main(sys.argv[1], sys.argv[2])
