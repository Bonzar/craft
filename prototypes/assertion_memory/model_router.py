"""Explicit semantic review routing; never substitute a writer for a failed review."""
import hashlib
import json

ROUTING_POLICY='semantic-task-review-v1'
REVIEW_TASKS=frozenset({
    'Verify thought',
    'Verify source thought batch',
    'Check ready thought',
    'Disambiguate thought support',
    'Verify contextual assertion interpretations',
    'Verify source interpretation withdrawals',
    'Check source withdrawals against full-source coverage',
    'Verify semantic evidence links',
    'Review source material coverage',
    'Review thematic source membership',
    'Review attributed thematic map',
    'Review grounded memory overview',
    'Review grounded memory contribution',
    'Review memory view name',
})


def route_profile_key(provider,primary_model,review_model=None,reasoning_effort=None):
    identity={'provider':provider,'primary_model':primary_model,
              'review_model':review_model,'reasoning_effort':reasoning_effort,'routing_policy':ROUTING_POLICY}
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class TaskModelRouter:
    """model_for(task) exposes the actual backend and its requested_model label.

    That label is a requested configuration, never an actual served-model claim.
    Usage remains recorded by each underlying provider's normal usage ledger.
    """
    def __init__(self,primary,review,*,profile_key):
        if not callable(primary) or not callable(review):
            raise TypeError('primary and review providers must both be callable')
        self.primary,self.review=primary,review
        self.route_profile_key=profile_key

    def model_for(self,task):
        return self.review if task in REVIEW_TASKS else self.primary

    def __call__(self,request):
        # Legacy overview.task can be the user's free-form question. Its stable
        # usage_task identifies generation even if that question resembles a task.
        task=getattr(request,'usage_task',None) or request.task
        return self.model_for(task)(request)
