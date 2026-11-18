"""Generate a per-configuration macOS LaunchAgent for worker recovery."""

from __future__ import annotations

import argparse
import hashlib
import plistlib
import sys
from pathlib import Path

from .desktop import load_config


def launch_agent(config_path: Path, *, python: str = sys.executable, interval: int = 30) -> dict:
    if type(interval) is not int or interval < 1:
        raise ValueError("launch interval must be a positive number of seconds")
    config_path = config_path.resolve(strict=True)
    config = load_config(config_path)
    if not config.get("cwd_roots"):
        raise ValueError("worker recovery requires an explicit project allowlist")
    key = hashlib.sha256(str(config_path).encode()).hexdigest()[:20]
    script = Path(__file__).resolve().with_name("desktop.py")
    log = Path(config["db"]).with_suffix(".launchd.log")
    return {
        "Label": f"local.assertion-memory.{key}",
        "ProgramArguments": [python, str(script), "worker", "--config", str(config_path)],
        "RunAtLoad": True,
        "StartInterval": interval,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        agent = launch_agent(args.config)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        Path(agent["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / (agent["Label"] + ".plist")
        content = plistlib.dumps(agent)
        try:
            with path.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(f"existing LaunchAgent has different settings: {path}")
        print(path.resolve())
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
