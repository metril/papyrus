import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.encryption_key:
            raise RuntimeError(
                "PAPYRUS_ENCRYPTION_KEY is not set. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )
        _fernet = Fernet(settings.encryption_key.encode())
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext back to plaintext."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def is_encrypted(value: str) -> bool:
    """Whether `value` is a valid Fernet token produced by `encrypt_value`,
    as opposed to a legacy plaintext value stored before encryption-at-rest
    was added for its field (F5)."""
    try:
        _get_fernet().decrypt(value.encode())
        return True
    except InvalidToken:
        return False


_warned_legacy_secret_hashes: set[str] = set()


def decrypt_value_lenient(value: str) -> str:
    """Decrypt `value`, or return it unchanged if it isn't a valid Fernet
    token at all (F5's legacy-secret ruling): fields like
    `Scanner.post_scan_config["ftp_password"]` used to be stored in
    cleartext, so a scanner configured before encryption-at-rest was added
    for that field -- and not yet re-saved since -- still holds a plaintext
    value here. Raising (and dropping the whole delivery action, as
    `decrypt_value` would via `InvalidToken`) would silently break
    auto-deliver for every pre-existing installation.

    Logs a warning the first time a given legacy value is seen (keyed by a
    truncated hash, never the secret itself) so it doesn't repeat on every
    delivery, but stays visible so it can be tracked down and migrated (a
    PUT that keeps the value via the "*set*" sentinel re-encrypts it).
    """
    try:
        return decrypt_value(value)
    except InvalidToken:
        digest = hashlib.sha256(value.encode()).hexdigest()[:12]
        if digest not in _warned_legacy_secret_hashes:
            _warned_legacy_secret_hashes.add(digest)
            logger.warning(
                "Found a legacy plaintext secret (not Fernet-encrypted) -- "
                "treating it as plaintext until it's re-saved [%s]", digest
            )
        return value
