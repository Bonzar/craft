"""Configured Visual SSH account binding; reuse legacy host/argv machinery."""
from contextlib import contextmanager

from ssh_transport import route as _existing_route

_ACCOUNTS = {
    "assertion-configured-visual-test-deploy": "assertion-visual-test-deploy",
    "assertion-configured-visual-prod-deploy": "assertion-visual-prod-deploy",
}


@contextmanager
def route(*, host, port, account, host_pin, key, runtime_dir):
    try:
        shape_account = _ACCOUNTS[account]
    except KeyError:
        raise ValueError("Unsupported configured Visual account") from None
    # The existing builder validates host/port/pin and owns key cleanup. It
    # never connects. Only the single compiled, exact configured destination
    # differs; we refuse any future change of existing argv shape.
    with _existing_route(host=host, port=port, account=shape_account, host_pin=host_pin,
                         key=key, runtime_dir=runtime_dir) as argv:
        if argv[-2:] != ["--", shape_account + "@" + host] or argv[0] != "/usr/bin/ssh":
            raise ValueError("Unexpected SSH route shape")
        yield [*argv[:-1], account + "@" + host]
