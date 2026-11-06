#!/usr/bin/env python3
"""Prepared by the module, works both from a source checkout and installed copy."""
from pathlib import Path
import sys
module_dir = Path(__file__).resolve().parents[1]
core = module_dir / '_core'
if not core.is_dir():
    core = module_dir.parents[1] / 'core'
sys.path.insert(0, str(core))
from jarvis import manifest, registry
library = registry.library_of(manifest.load(module_dir)).load()
raise SystemExit(library.main(module_dir))
