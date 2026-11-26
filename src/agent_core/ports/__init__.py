"""Ports: the interfaces Engines depend on. Adapters implement them.

Ports depend only on ``agent_core.types``. Concrete adapters (SQLite, model
providers, network, files) are added by the work package that needs them.
"""
