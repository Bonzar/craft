"""Declarative Visual bindings and project-specific operation hooks.

Caller exclusively owns workflow authority, approval, GitHub ZIP digest,
same-run receipt and stage scheduling. No GitHub API is called here.
"""
from dataclasses import dataclass
from pathlib import Path
import importlib
from importlib.machinery import ModuleSpec
import sys
from types import ModuleType
from configured_provider_checks import Content, Profile, ReceiptLayout, Target

_PACKAGE = "_assertion_configured_visual_ops"
_DIRECTORY = Path(__file__).resolve().parent


def _operation(name):
    # The trusted package is bound to this checked-out control module's own
    # directory. No project/tag data or bare import chooses an implementation.
    module = sys.modules.get(_PACKAGE)
    if module is None:
        module = ModuleType(_PACKAGE)
        module.__path__ = [str(_DIRECTORY)]
        module.__spec__ = ModuleSpec(_PACKAGE, loader=None, is_package=True)
        module.__spec__.submodule_search_locations = module.__path__
        module = sys.modules.setdefault(_PACKAGE, module)
    if getattr(module, "__path__", None) != [str(_DIRECTORY)]:
        raise ValueError("Wrong configured Visual package directory")
    return importlib.import_module(f"{_PACKAGE}.{name}")


@dataclass(frozen=True)
class Payload:
    directory: Path
    revision: str
    payload_sha: str
    release_digest: str


PROFILE = Profile(
    name="visual",
    original_workflow="assertion-memory-visual.yml",
    control_workflow="configured-release.yml",
    reusable_workflow="configured-release-reusable.yml",
    test=Target("configured-release-visual-test", "test",
                "assertion-cfg-vis-test-deploy", "deploy-visual"),
    prod=Target("configured-release-visual-prod", "prod",
                "assertion-cfg-vis-prod-deploy", "deploy-visual"),
    receipt=ReceiptLayout(archive_key="archive_sha256", manifest_key="release"),
)
TARGETS = {"test": PROFILE.test, "prod": PROFILE.prod}


def unpack_verified(archive, directory, revision):
    """Validate Visual's inner archive format; common owns outer ZIP bytes."""
    folder = Path(directory)
    payload_sha, release_digest = _operation("lifecycle").unpack(Path(archive), folder, revision)
    return Payload(folder, revision, payload_sha, release_digest)


def content(payload):
    return Content(payload.payload_sha, payload.release_digest)


def activate(stage, payload, transport, runtime=None):
    target = TARGETS[stage]
    # Non-application stage dispatch supplies scoped configuration, never a
    # shared environment dict. This compile-time binding rejects wrong routes.
    if transport["account"] != target.receiver_account:
        raise ValueError("Wrong configured Visual transport identity")
    return _operation("visual_send").deploy({
        "VISUAL_ENV": stage,
        "VISUAL_DOWNLOAD": str(payload.directory),
        "VISUAL_REVISION": payload.revision,
        "VISUAL_SHA": payload.payload_sha,
        "VISUAL_DIGEST": payload.release_digest,
        "VISUAL_HOST": transport["host"],
        "VISUAL_PORT": transport["port"],
        "VISUAL_HOST_PIN": transport["host_pin"],
        "VISUAL_SSH_KEY": transport["ssh_key"],
        "VISUAL_SIGNING_KEY": transport["signing_key"],
        "RUNNER_TEMP": transport["runner_temp"],
    }, lane="configured", **({"runtime": runtime} if runtime is not None else {}))


def verify_smoke(stage, payload, target):
    TARGETS[stage]
    _operation("smoke").main({
        "VISUAL_ENV": stage,
        "VISUAL_DOWNLOAD": str(payload.directory),
        "VISUAL_DIGEST": payload.release_digest,
        "VISUAL_GATEWAY_HOST": target["gateway_host"],
        "VISUAL_GATEWAY_TOKEN": target["gateway_token"],
    })


def stage_required_runtime(stage, setting, *, get, original, static_sha, destination):
    return _operation("runtime_consumer").stage_test_if_required(
        stage, setting, get=get, original=original, static_sha=static_sha, destination=destination)
