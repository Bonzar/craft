"""Read-only controller proof access without configuring a model or a worker."""
from .engine import MemoryEngine


class _ProofReader:
    # Existing readers have no model calls or writes. Construction deliberately
    # does not initialize MemoryEngine, its schemas, a provider, or any queue.
    _spec = MemoryEngine._spec
    get_version = MemoryEngine.get_version
    verify_overviews = True
    model = object()  # Require the ordinary strict accepted-review predicate.

    def __init__(self,store,pipeline_key):
        self.store=store
        self.pipeline_key=pipeline_key


def validate_stored_completion(store,pipeline_key,scope,result,operation):
    from .background_overviews import validate_controller_completion
    return validate_controller_completion(_ProofReader(store,pipeline_key),scope,result,operation=operation)
