"""
WealthWatch License Key Generator
──────────────────────────────────
INTERNAL TOOL — DO NOT DISTRIBUTE
This file must NEVER be included in the build or given to customers.

Usage:
    python keygen.py "Customer Name"
"""
import sys
import hashlib
import hmac
from datetime import date

# Same key as in licensing.py — kept here for generation
_HMAC_KEY = bytes.fromhex('a3f7c91d2e0b48569f1c7d3a5e8b024d6f9a1c3e5b7d2f4a6c8e0b1d3f5a7c9e')


def generate_license_key(owner_name):
    """Generate a valid HMAC-verified license key."""
    seed = f'{owner_name}:{date.today().isoformat()}'
    payload = hashlib.sha256(seed.encode()).hexdigest()[:10].upper()
    tag = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()[:6].upper()
    key = payload + tag
    return f'{key[:4]}-{key[4:8]}-{key[8:12]}-{key[12:16]}'


def validate_key(key):
    """Verify a key is valid."""
    key = key.replace('-', '').upper()
    if len(key) != 16:
        return False
    payload = key[:10]
    tag = key[10:16]
    expected = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()[:6].upper()
    return hmac.compare_digest(tag, expected)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python keygen.py "Customer Name"')
        sys.exit(1)

    name = ' '.join(sys.argv[1:])
    key = generate_license_key(name)
    valid = validate_key(key)

    print(f'  Owner:    {name}')
    print(f'  Key:      {key}')
    print(f'  Valid:    {valid}')
