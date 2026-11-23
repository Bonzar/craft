"""Protected reusable job entrypoint; never check out or execute caller source."""
import os
from pathlib import Path

import lifecycle
import test_producer


if __name__ == '__main__':
    sha = lifecycle.dispatch_context(os.environ)
    test_producer.verify(lifecycle.ci.api,lifecycle.ROOT,sha,os.environ['AUDITED_REUSABLE_SHA'],Path.cwd())
