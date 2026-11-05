"""
Shared helper methods for HorillaUser account provisioning.
"""

import secrets
import string


def generate_unique_fallback_username() -> str:
    """
    Returns a random 5-letter username guaranteed not to collide with any
    existing HorillaUser.username. Used when an employee's email is
    already taken as a username -- e.g. by an orphaned account left behind
    by a deleted employee -- so account creation can fall back to this
    instead of crashing on the uniqueness constraint.
    """
    from horilla_auth.models import HorillaUser

    while True:
        candidate = "".join(secrets.choice(string.ascii_lowercase) for _ in range(5))
        if not HorillaUser.objects.filter(username=candidate).exists():
            return candidate


def generate_random_password(length: int = 12) -> str:
    """
    Returns a cryptographically secure random password containing at least
    one lowercase letter, one uppercase letter and one digit.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in password)
            and any(char.isupper() for char in password)
            and any(char.isdigit() for char in password)
        ):
            return password
