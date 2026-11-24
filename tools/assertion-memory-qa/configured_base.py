"""Configured Base project declaration. No release validation or deployment side effects."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'assertion-memory-release'/'experimental'))
from configured_provider_checks import Profile, ReceiptLayout, Target

BASE=Profile(
    name='base',
    original_workflow='assertion-memory-base.yml',
    control_workflow='configured-release.yml',
    reusable_workflow='configured-release-reusable.yml',
    test=Target('configured-release-base-test','test','assertion-cfg-base-test-deploy','configured-base-test'),
    prod=Target('configured-release-base-prod','production','assertion-cfg-base-prod-deploy','deploy-release'),
    receipt=ReceiptLayout(archive_key='sha256',status_key='status'),
)
