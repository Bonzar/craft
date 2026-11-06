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
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
VERSION = '1.24.7'
MINIMUM = (1, 24, 7)
SHA256 = 'da18191ddb7db8a9339816f3e2b54bdded8047cdc2a5d67059478f8d1595c43f'
URL = f'https://go.dev/dl/go{VERSION}.linux-amd64.tar.gz'
# Архив proxy.golang.org сверен с h1 из sum.golang.org (Go dirhash).
DARWIN_ARM64_SHA256 = 'b529787fe1ee7c4a1e5b9635e97ff0840d1206b7cccc958ec1f8296fac604f63'
DOWNLOAD_TIMEOUT = 60
VERSION_TIMEOUT = 15
RETRY_DELAYS = (1, 2, 4)


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
    target = (platform.system(), platform.machine())
    archives = {
        ('Linux', 'x86_64'): (URL, SHA256),
        ('Darwin', 'arm64'): (f'https://proxy.golang.org/golang.org/toolchain/@v/v0.0.1-go{VERSION}.darwin-arm64.zip', DARWIN_ARM64_SHA256),
    }
    if target not in archives:
        raise ValueError(f'автозагрузка Go не поддерживает {target}, нужен совместимый go на PATH')
    url, checksum = archives[target]
    with tempfile.TemporaryDirectory(prefix='.go-install-', dir=root) as temporary:
        stage = Path(temporary)
        archive = stage / 'go.tar.gz'
        if download(archive, url) != checksum:
            raise ValueError('SHA256 архива Go не совпал, программа не установлена')
        extract(archive, stage, url)
        tool = stage / 'go/bin/go'
        if not compatible(str(tool), environment):
            raise ValueError('скачанный Go не прошёл проверку версии')
        os.replace(stage / 'go', root / 'go')


def extract(archive: Path, stage: Path, url: str) -> None:
    if not url.endswith('.zip'):
        with tarfile.open(archive, mode='r:gz') as bundle:
            bundle.extractall(stage, filter='data')
        return
    # ZipFile не восстанавливает executable-биты компилятора и его инструментов.
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            extracted = Path(bundle.extract(entry, stage))
            if not entry.is_dir():
                extracted.chmod((entry.external_attr >> 16) & 0o777)
        source = stage.joinpath(*bundle.namelist()[0].split('/')[:2])
    os.replace(source, stage / 'go')


def download(archive: Path, url: str = URL) -> str:
    for attempt in range(len(RETRY_DELAYS) + 1):
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}),
                                        timeout=DOWNLOAD_TIMEOUT) as response, archive.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            return digest.hexdigest()
        except (urllib.error.URLError, TimeoutError) as error:
            permanent = isinstance(error, urllib.error.HTTPError) and error.code not in (408, 429) and error.code < 500
            if permanent or attempt == len(RETRY_DELAYS):
                raise
            time.sleep(RETRY_DELAYS[attempt])
    raise RuntimeError('исчерпаны попытки загрузки Go')
