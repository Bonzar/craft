#!/usr/bin/env python3
"""The module's own native hook delivers its shared context and capture adapter."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'_core'))
import jarvis
from jarvis import registry,wrappers


class Module(jarvis.Module):
    def handle(self,event,runtime):
        library=registry.library_of(runtime.manifest)
        if library is None:raise RuntimeError('assertion memory library is missing')
        text=library.load().context(event,runtime.storage)
        return jarvis.Context(text) if text else jarvis.Silence()


if __name__=='__main__':sys.exit(wrappers.run_hook(__file__,Module))
