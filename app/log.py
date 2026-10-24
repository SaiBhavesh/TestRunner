"""Console progress log for the worker and adapters. Never log keys or tokens."""
import logging
import sys

log = logging.getLogger("testrunner")
if not log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [testrunner] %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False
