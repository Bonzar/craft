"""Independent query evidence, source transport and answer delivery contract."""
UNASSESSED = object()


def project(response, *, processing_gap=UNASSESSED, missing_roots=False):
    """Describe only conclusions supported by the serving path.

    Query evidence is bounded by retrieved records, never the whole database,
    and global queue/transport counters alone cannot assess question relevance.
    """
    if response.get('answer_error') or response.get('generation_error'):
        evidence='answer_failed'
    elif response.get('partial_answer'):
        evidence='partial'
    elif processing_gap is True or missing_roots or response.get('knowledge_limit') and response.get('answer_accepted'):
        evidence='qualified'
    elif response.get('answer_accepted'):
        evidence='supported'
    elif response.get('knowledge_limit') or response.get('answer_metadata',{}).get('notice'):
        evidence='not_established'
    else:
        evidence='unreviewed'

    if processing_gap is True or missing_roots:
        gap='relevant'
    elif processing_gap is False:
        gap='none_identified'
    else:
        gap='unassessed'

    transport=response.get('transport_status') or {}
    if transport.get('transport_limit') and (transport.get('source_gap_count',0)>0
                                              or transport.get('known_pending_count',0)>0):
        transport_state='known_delivery_gap'
    elif transport.get('coverage')=='reported_current':
        transport_state='reported_current'
    else:
        transport_state='unobserved'

    return {'evidence':evidence,'evidence_scope':'retrieved_records','processing_gap':gap,
            'transport':{'status':transport_state,'scope':'global','query_relevance':'unassessed'},
            'answer_delivery': ('paged' if response.get('answer_continuations') else
                                'inline' if response.get('answer_payload_complete') and
                                    (response.get('answer') or response.get('view',{}).get('summary')) else
                                'not_delivered')}
