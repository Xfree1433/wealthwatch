"""
WealthWatch Extensions
Shared extension instances for Flask app.
"""
# Currently WealthWatch uses manual CSRF and session-based auth (no Flask-Login).
# This module provides shared utilities used across blueprints.

from functools import wraps
from flask import session, request, redirect, url_for, jsonify
from markupsafe import escape
import re
from datetime import date

VALID_ACCOUNT_TYPES = {'checking', 'savings', 'credit', 'investment', 'real_estate', 'loan'}
VALID_FREQUENCIES = {'monthly', 'weekly', 'annual'}
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _safe(val):
    """Escape user string for safe HTML embedding."""
    if val is None:
        return ''
    return str(escape(str(val)))


def _validate_date(d):
    """Return ISO date string or None if invalid."""
    if not d or not DATE_RE.match(str(d)):
        return None
    try:
        date.fromisoformat(str(d))
        return str(d)
    except ValueError:
        return None


def _err(msg, code=400):
    return jsonify({'error': msg}), code


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get('authed'):
            if request.path.startswith('/api/'):
                return _err('Unauthorized', 401)
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return wrapped
