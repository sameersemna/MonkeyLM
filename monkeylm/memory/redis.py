"""Redis state cache - re-exported from PersistenceEngine in postgres.py.

Redis functionality is embedded in the PersistenceEngine class. This module
provides direct access to Redis-specific helpers for convenience.
"""

from monkeylm.memory.postgres import PersistenceEngine

__all__ = ["PersistenceEngine"]
