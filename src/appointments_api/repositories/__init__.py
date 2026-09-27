"""Persistence layer: SQLAlchemy repositories and the Redis refresh-token store.

Repositories are the only place that touches a database session or Redis. Services and routers
depend on the Protocols these classes implement, never on SQLAlchemy or redis-py directly.
"""
