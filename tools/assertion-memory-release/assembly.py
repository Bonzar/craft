"""Common byte-qualification step for independently built product inputs."""
from pathlib import Path
import tempfile

from release_core import unpack


def inspect(artifact, expected_sha, *, spec, validate, runtime_dir=None):
    """Validate without rewriting, publishing or installing the assembled bytes."""
    with tempfile.TemporaryDirectory(prefix='release-assembly-',dir=runtime_dir) as root:
        with Path(artifact).open('rb') as stream:
            payload=unpack(stream,Path(root),expected_sha,spec)
        return validate(payload)
