"""
WealthWatch License Manager
─────────────────────────────
Demo: 30-day trial with limits
Licensed: Full access
"""
import os
import hashlib
import hmac
import json
from datetime import date, datetime

APP_NAME = 'WealthWatch'
# Validation uses HMAC — the signing key is NOT distributed.
# Only validate_key() ships; generate_license_key() is in keygen.py (internal only).
_HMAC_KEY = bytes.fromhex('a3f7c91d2e0b48569f1c7d3a5e8b024d6f9a1c3e5b7d2f4a6c8e0b1d3f5a7c9e')

DEMO_LIMITS = {
    'max_accounts': 2,
    'max_transactions': 50,
    'max_holdings': 5,
    'allow_import': False,
    'allow_export': False,
    'allow_backup': False,
    'trial_days': 30,
}


def _data_dir():
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        d = os.path.join(base, 'WealthWatch')
    else:
        d = os.path.expanduser('~/.wealthwatch')
    os.makedirs(d, exist_ok=True)
    return d


def _license_path():
    return os.path.join(_data_dir(), '.license')


def _trial_path():
    return os.path.join(_data_dir(), '.trial')


def validate_key(key):
    """Check if a license key is valid using HMAC verification."""
    key = key.strip().upper().replace('-', '')
    if len(key) != 16 or not all(c in '0123456789ABCDEF' for c in key):
        return False
    payload = key[:10]
    tag = key[10:16]
    expected = hmac.new(_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()[:6].upper()
    return hmac.compare_digest(tag, expected)


def get_trial_start():
    path = _trial_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return date.fromisoformat(data['start'])
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    today = date.today()
    with open(path, 'w') as f:
        json.dump({'start': today.isoformat(), 'app': APP_NAME}, f)
    return today


def get_license_status():
    path = _license_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            key = data.get('key', '')
            if validate_key(key):
                return {
                    'licensed': True, 'demo': False, 'expired': False,
                    'days_remaining': None, 'key': key, 'limits': None
                }
        except (json.JSONDecodeError, IOError):
            pass

    trial_start = get_trial_start()
    days_used = (date.today() - trial_start).days
    days_remaining = max(DEMO_LIMITS['trial_days'] - days_used, 0)
    expired = days_remaining <= 0

    return {
        'licensed': False, 'demo': True, 'expired': expired,
        'days_remaining': days_remaining, 'key': None, 'limits': DEMO_LIMITS
    }


def activate_license(key):
    key = key.strip().upper()
    if not validate_key(key):
        return False, 'Invalid license key.'
    formatted = f'{key[:4]}-{key[4:8]}-{key[8:12]}-{key[12:16]}' if '-' not in key else key
    path = _license_path()
    with open(path, 'w') as f:
        json.dump({'key': formatted, 'activated': datetime.now().isoformat(), 'app': APP_NAME}, f)
    return True, 'License activated successfully.'


def deactivate_license():
    path = _license_path()
    if os.path.exists(path):
        os.remove(path)


def data_dir():
    return _data_dir()
