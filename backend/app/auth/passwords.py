"""Password hashing with Argon2id.

Argon2id is the current recommendation for password storage: memory-hard, so a
stolen hash is expensive to attack offline. Parameters follow the OWASP
guidance (19 MiB, 2 iterations, 1 lane), which a laptop handles in well under
a second while costing an attacker real memory per guess.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: OWASP's second-choice Argon2id profile: m=19456 KiB, t=2, p=1.
_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1, hash_len=32, salt_len=16)

#: Shortest password the application will accept.
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    """Hash a password for storage. The result embeds its own parameters."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Check a password against a stored hash.

    Returns False for a wrong password and for a malformed hash; it never
    raises, so a corrupted row cannot turn a failed login into a 500 that
    leaks which usernames exist.
    """
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when a hash was made with weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
