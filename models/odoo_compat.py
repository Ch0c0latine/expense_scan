"""What differs between Odoo 19 and Odoo 20 for this module."""
from psycopg2 import errors

# Exceptions after which a transaction is replayed (exported from another module in Odoo 19).
PG_CONCURRENCY_EXCEPTIONS_TO_RETRY = (
    errors.LockNotAvailable, errors.SerializationFailure, errors.DeadlockDetected)


def binary_bytes(value):
    """The bytes held by a Binary field or by the raw field of an attachment (a BinaryValue since Odoo 20)."""
    if not value:
        return b""
    return value.content if hasattr(value, "content") else bytes(value)
