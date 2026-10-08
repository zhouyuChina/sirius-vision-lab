import hashlib

from sirius_vision.auth import verify_password


def test_verify_password_requires_documented_64_byte_digest():
    password = 'regression-test-password'
    salt = bytes(range(16))
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    short_digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)

    assert verify_password(password, f'scrypt${salt.hex()}${digest.hex()}')
    assert not verify_password(password, f'scrypt${salt.hex()}${short_digest.hex()}')
