"""Go и рабочие кэши принадлежат модулю, без go env -w и общих ссылок."""

import fcntl
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
VERSION = '1.24.7'
MINIMUM = (1, 24, 7)
SHA256 = 'da18191ddb7db8a9339816f3e2b54bdded8047cdc2a5d67059478f8d1595c43f'
URL = f'https://go.dev/dl/go{VERSION}.linux-amd64.tar.gz'
DOWNLOAD_TIMEOUT = 60
VERSION_TIMEOUT = 15


def compatible(tool: str, environment: dict) -> bool:
    try:
        done = subprocess.run([tool, 'version'], env=environment,
                              capture_output=True, text=True, timeout=VERSION_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    version = re.fullmatch(r'go version go(\d+)\.(\d+)\.(\d+) \S+\s*', done.stdout)
    return done.returncode == 0 and version is not None and tuple(map(int, version.groups())) >= MINIMUM


def prepare() -> tuple[str, dict]:
    """Вызывается сборщиком синхронно, не отдельным параллельным хуком."""
    root = MODULE_DIR / 'bin'
    root.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'GOCACHE': str(root / 'cache/build'),
                   'GOPATH': str(root / 'cache/packages'),
                   'GOMODCACHE': str(root / 'cache/packages/pkg/mod'), 'GOTOOLCHAIN': 'local'}
    for name in ('GOCACHE', 'GOPATH', 'GOMODCACHE'):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    tool = shutil.which('go')
    if tool and compatible(tool, environment):
        return tool, environment
    # Оба сборщика могут стартовать одновременно в одном окружении.
    with (root / '.prepare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        local = root / 'go/bin/go'
        if local.is_file() and compatible(str(local), environment):
            return str(local), environment
        if (root / 'go').exists():
            raise ValueError(f'локальный Go повреждён или несовместим: {local}')
        install(root, environment)
        return str(local), environment


def install(root: Path, environment: dict) -> None:
    if (platform.system(), platform.machine()) != ('Linux', 'x86_64'):
        raise ValueError('автозагрузка Go поддерживает Linux x86_64, нужен совместимый go на PATH')
    with tempfile.TemporaryDirectory(prefix='.go-install-', dir=root) as temporary:
        stage = Path(temporary)
        archive = stage / 'go.tar.gz'
        digest = hashlib.sha256()
        with urllib.request.urlopen(URL, timeout=DOWNLOAD_TIMEOUT) as response, archive.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != SHA256:
            raise ValueError('SHA256 архива Go не совпал, программа не установлена')
        with tarfile.open(archive, mode='r:gz') as bundle:
            bundle.extractall(stage, filter='data')
        tool = stage / 'go/bin/go'
        if not compatible(str(tool), environment):
            raise ValueError('скачанный Go не прошёл проверку версии')
        os.replace(stage / 'go', root / 'go')

