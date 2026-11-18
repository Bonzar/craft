"""Provider fixtures for tests of transport and recovery, not semantic accuracy."""
import json


def with_literal_verification(provider):
    """Let existing literal-response fixtures exercise the added verification phase."""
    def respond(request):
        if request.task == 'Review source material coverage':
            # This transport fixture never judged source inventory completeness.
            # Keep its old claim checks usable without silently certifying coverage.
            return json.dumps({'source_coverage': {'status': 'unresolved', 'omissions': [],
                'reason': 'Literal transport fixture does not assess source completeness.'}})
        if request.task in {'Verify contextual assertion interpretations', 'Verify semantic evidence links'}:
            verdicts = [{'index': row['index'], 'status': 'accepted', 'reason': 'Literal fixture assertion.'}
                        for row in request.records if row.get('role') in {'interpretation', 'proposed_link'}]
            return json.dumps({'verdicts': verdicts})
        return provider(request)
    return respond
