"""Runtime artifact stores and workflow artifact builders.

Submodules in this package own durable JSON boundaries under BAI_HOME/state or
BAI_HOME/memory. They do not execute effects; execution authority stays in the
Harness/effects layer.
"""
