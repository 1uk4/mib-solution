#!/usr/bin/env python3
"""Entry point — dispatches to the active version's pipeline.

Switch versions by changing the import below. Each `vN/` directory is a
self-contained pipeline with its own docs (`EDGE_CASES.md`, `ITERATIONS.md`).
"""
import sys

from v3.solution import main, VERSION  # active pipeline


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: solution.py <input_pdf_dir> <output_path>  (version={VERSION})")
    main(sys.argv[1], sys.argv[2])
