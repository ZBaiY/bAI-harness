"""Harness execution, effects, and scheduler artifact components.

Execution remains serial in phase one. The modules here coordinate one bounded
request, narrow effect adapters, trusted test subprocesses, and metadata-only
scheduler records without a daemon or worker loop.
"""
