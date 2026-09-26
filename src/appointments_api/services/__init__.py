"""Business logic layer.

Everything here is pure Python: no FastAPI, no SQLAlchemy session, no HTTP client. Anything
from the outside world arrives through a ``typing.Protocol`` (a ``Clock``, a repository), so the
service layer can be unit-tested with in-memory fakes and no I/O at all.
"""
