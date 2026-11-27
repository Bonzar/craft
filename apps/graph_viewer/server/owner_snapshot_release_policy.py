"""Versioned release selection for the existing owner stream; no host settings."""

# Set by release source review, not by a browser/request/environment input.
# TEST and PROD install the same bytes. A later reviewed release can add PROD
# only after its independently validated ordinary ingress witness.
OWNER_STREAM_TEST = True
OWNER_STREAM_PROD = False


def enabled(*, dashboard, browser, explicit_test=False):
    """Call only after configure_owner_lanes validates the protected binding."""
    scoped_test = (getattr(dashboard, 'stage', None) == 'test'
                   and getattr(browser, 'stage', None) == 'test')
    scoped_prod = (getattr(dashboard, 'stage', None) == 'prod'
                   and getattr(browser, 'stage', None) == 'prod')
    if type(explicit_test) is not bool or explicit_test and not scoped_test:
        raise ValueError('Owner snapshot flag requires the protected TEST owner lane')
    return ((scoped_test and (OWNER_STREAM_TEST or explicit_test))
            or (scoped_prod and OWNER_STREAM_PROD))
