"""
Accounts Blueprint - Account CRUD
"""
from flask import Blueprint, render_template, jsonify, request, current_app
from app.extensions import login_required, _safe, _err, _validate_date, VALID_ACCOUNT_TYPES
from app.services.database import get_db, snapshot_net_worth
from app.services.licensing import get_license_status
from functools import wraps
from datetime import date

accounts_bp = Blueprint('accounts', __name__)


def _demo_check_accounts(f):
    """Enforce demo limits on account creation."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        lic = get_license_status()
        if lic['licensed']:
            return f(*args, **kwargs)
        if lic['expired']:
            return _err('Trial expired. Please activate a license to continue.', 403)
        if lic['limits']:
            max_val = lic['limits'].get('max_accounts')
            if max_val is not None:
                db = get_db(current_app)
                current = db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
                if current >= max_val:
                    return _err(f'Demo limit reached: max {max_val} accounts. Activate a license for unlimited access.', 403)
        return f(*args, **kwargs)
    return wrapped


# ── Page ─────────────────────────────────────────────────────────────────────

@accounts_bp.route('/accounts')
@login_required
def accounts():
    return render_template('accounts.html', active='accounts')


# ── API: Read ────────────────────────────────────────────────────────────────

@accounts_bp.route('/api/accounts')
@login_required
def api_accounts():
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM accounts ORDER BY type, name').fetchall()
    return jsonify([dict(r) for r in rows])


# ── API: Create ──────────────────────────────────────────────────────────────

@accounts_bp.route('/api/accounts', methods=['POST'])
@login_required
@_demo_check_accounts
def api_accounts_create():
    d = request.get_json()
    if not d or not d.get('name', '').strip():
        return _err('Account name is required.')
    if d.get('type') not in VALID_ACCOUNT_TYPES:
        return _err('Invalid account type.')
    try:
        balance = round(float(d.get('balance', 0)), 2)
    except (ValueError, TypeError):
        return _err('Invalid balance.')
    db = get_db(current_app)
    cur = db.execute(
        'INSERT INTO accounts (name, institution, type, balance, last_updated) VALUES (?,?,?,?,?)',
        (_safe(d['name']).strip(), _safe(d.get('institution', '')).strip(),
         d['type'], balance, date.today().isoformat())
    )
    db.commit()
    snapshot_net_worth(current_app)
    row = db.execute('SELECT * FROM accounts WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


# ── API: Update ──────────────────────────────────────────────────────────────

@accounts_bp.route('/api/accounts/<int:aid>', methods=['PUT'])
@login_required
def api_accounts_update(aid):
    d = request.get_json()
    if not d or not d.get('name', '').strip():
        return _err('Account name is required.')
    if d.get('type') not in VALID_ACCOUNT_TYPES:
        return _err('Invalid account type.')
    try:
        balance = round(float(d.get('balance', 0)), 2)
    except (ValueError, TypeError):
        return _err('Invalid balance.')
    db = get_db(current_app)
    db.execute(
        'UPDATE accounts SET name=?, institution=?, type=?, balance=?, last_updated=? WHERE id=?',
        (_safe(d['name']).strip(), _safe(d.get('institution', '')).strip(),
         d['type'], balance, date.today().isoformat(), aid)
    )
    db.commit()
    snapshot_net_worth(current_app)
    row = db.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
    if not row:
        return _err('Account not found.', 404)
    return jsonify(dict(row))


# ── API: Delete ──────────────────────────────────────────────────────────────

@accounts_bp.route('/api/accounts/<int:aid>', methods=['DELETE'])
@login_required
def api_accounts_delete(aid):
    db = get_db(current_app)
    db.execute('DELETE FROM accounts WHERE id=?', (aid,))
    db.commit()
    snapshot_net_worth(current_app)
    return jsonify({'ok': True})
