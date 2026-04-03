"""
Transactions Blueprint - Transaction CRUD, import/export, transfers, splits, recurring, receipts, bulk ops
"""
from flask import Blueprint, render_template, jsonify, request, Response, current_app, send_from_directory
from app.extensions import login_required, _safe, _err, _validate_date, VALID_FREQUENCIES
from app.services.database import get_db, snapshot_net_worth, process_recurring, auto_categorize
from app.services.licensing import get_license_status
from functools import wraps
from datetime import date
import os
import secrets
import csv
import io

transactions_bp = Blueprint('transactions', __name__)


def _demo_check_transactions(f):
    """Enforce demo limits on transaction creation."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        lic = get_license_status()
        if lic['licensed']:
            return f(*args, **kwargs)
        if lic['expired']:
            return _err('Trial expired. Please activate a license to continue.', 403)
        if lic['limits']:
            max_val = lic['limits'].get('max_transactions')
            if max_val is not None:
                db = get_db(current_app)
                current = db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
                if current >= max_val:
                    return _err(f'Demo limit reached: max {max_val} transactions. Activate a license for unlimited access.', 403)
        return f(*args, **kwargs)
    return wrapped


def _demo_check_holdings(f):
    """Enforce demo limits on holdings creation."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        lic = get_license_status()
        if lic['licensed']:
            return f(*args, **kwargs)
        if lic['expired']:
            return _err('Trial expired. Please activate a license to continue.', 403)
        if lic['limits']:
            max_val = lic['limits'].get('max_holdings')
            if max_val is not None:
                db = get_db(current_app)
                current = db.execute('SELECT COUNT(*) FROM holdings').fetchone()[0]
                if current >= max_val:
                    return _err(f'Demo limit reached: max {max_val} holdings. Activate a license for unlimited access.', 403)
        return f(*args, **kwargs)
    return wrapped


def _licensed_only(f):
    """Block feature entirely in demo mode."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        lic = get_license_status()
        if not lic['licensed']:
            return _err('This feature requires a license. Activate to unlock.', 403)
        return f(*args, **kwargs)
    return wrapped


def _reconcile_balance(db, account_id, amount_delta):
    """Adjust account balance by amount_delta."""
    db.execute('UPDATE accounts SET balance = balance + ?, last_updated = ? WHERE id = ?',
               (round(amount_delta, 2), date.today().isoformat(), account_id))


# ── Page routes ──────────────────────────────────────────────────────────────

@transactions_bp.route('/transactions')
@login_required
def transactions():
    return render_template('transactions.html', active='transactions')


@transactions_bp.route('/recurring')
@login_required
def recurring():
    return render_template('recurring.html', active='recurring')


# ── API: Transactions Read ───────────────────────────────────────────────────

@transactions_bp.route('/api/transactions')
@login_required
def api_transactions():
    db = get_db(current_app)
    limit = request.args.get('limit', 50, type=int)
    date_from = _validate_date(request.args.get('from'))
    date_to = _validate_date(request.args.get('to'))

    sql = ('SELECT t.*, a.name as account_name FROM transactions t '
           'JOIN accounts a ON t.account_id = a.id ')
    params = []
    clauses = []
    if date_from:
        clauses.append('t.date >= ?')
        params.append(date_from)
    if date_to:
        clauses.append('t.date <= ?')
        params.append(date_to)
    if clauses:
        sql += 'WHERE ' + ' AND '.join(clauses) + ' '
    sql += 'ORDER BY t.date DESC LIMIT ?'
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ── API: Transactions Create ─────────────────────────────────────────────────

@transactions_bp.route('/api/transactions', methods=['POST'])
@login_required
@_demo_check_transactions
def api_transactions_create():
    d = request.get_json()
    if not d or not d.get('description', '').strip() or not d.get('category', '').strip():
        return _err('Description and category are required.')
    txn_date = _validate_date(d.get('date'))
    if not txn_date:
        return _err('Invalid date.')
    try:
        amount = round(float(d['amount']), 2)
        account_id = int(d['account_id'])
    except (ValueError, TypeError, KeyError):
        return _err('Invalid amount or account.')
    db = get_db(current_app)
    if not db.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone():
        return _err('Account not found.', 404)
    tags = ','.join([_safe(t).strip() for t in (d.get('tags') or '').split(',') if t.strip()])
    cur = db.execute(
        'INSERT INTO transactions (account_id, date, description, category, amount, notes, tags) VALUES (?,?,?,?,?,?,?)',
        (account_id, txn_date, _safe(d['description']).strip(), _safe(d['category']).strip(),
         amount, _safe(d.get('notes', '')).strip(), tags)
    )
    _reconcile_balance(db, account_id, amount)
    db.commit()
    snapshot_net_worth(current_app)
    row = db.execute(
        'SELECT t.*, a.name as account_name FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.id=?',
        (cur.lastrowid,)
    ).fetchone()
    return jsonify(dict(row)), 201


# ── API: Transactions Update ─────────────────────────────────────────────────

@transactions_bp.route('/api/transactions/<int:tid>', methods=['PUT'])
@login_required
def api_transactions_update(tid):
    d = request.get_json()
    if not d or not d.get('description', '').strip() or not d.get('category', '').strip():
        return _err('Description and category are required.')
    txn_date = _validate_date(d.get('date'))
    if not txn_date:
        return _err('Invalid date.')
    try:
        new_amount = round(float(d['amount']), 2)
        new_account_id = int(d['account_id'])
    except (ValueError, TypeError, KeyError):
        return _err('Invalid amount or account.')
    db = get_db(current_app)
    old = db.execute('SELECT account_id, amount FROM transactions WHERE id=?', (tid,)).fetchone()
    if not old:
        return _err('Transaction not found.', 404)
    _reconcile_balance(db, old['account_id'], -old['amount'])
    _reconcile_balance(db, new_account_id, new_amount)
    tags = ','.join([_safe(t).strip() for t in (d.get('tags') or '').split(',') if t.strip()])
    db.execute(
        'UPDATE transactions SET account_id=?, date=?, description=?, category=?, amount=?, notes=?, tags=? WHERE id=?',
        (new_account_id, txn_date, _safe(d['description']).strip(), _safe(d['category']).strip(),
         new_amount, _safe(d.get('notes', '')).strip(), tags, tid)
    )
    db.commit()
    snapshot_net_worth(current_app)
    row = db.execute(
        'SELECT t.*, a.name as account_name FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.id=?',
        (tid,)
    ).fetchone()
    return jsonify(dict(row))


# ── API: Transactions Delete ─────────────────────────────────────────────────

@transactions_bp.route('/api/transactions/<int:tid>', methods=['DELETE'])
@login_required
def api_transactions_delete(tid):
    db = get_db(current_app)
    old = db.execute('SELECT account_id, amount FROM transactions WHERE id=?', (tid,)).fetchone()
    if old:
        _reconcile_balance(db, old['account_id'], -old['amount'])
    db.execute('DELETE FROM transactions WHERE id=?', (tid,))
    db.commit()
    snapshot_net_worth(current_app)
    return jsonify({'ok': True})


# ── Transfers ────────────────────────────────────────────────────────────────

@transactions_bp.route('/api/transfers', methods=['POST'])
@login_required
def api_transfer():
    d = request.get_json()
    if not d:
        return _err('Request body required.')
    try:
        from_id = int(d['from_account_id'])
        to_id = int(d['to_account_id'])
        amount = round(float(d['amount']), 2)
    except (ValueError, TypeError, KeyError):
        return _err('Invalid transfer data.')
    if from_id == to_id:
        return _err('Cannot transfer to the same account.')
    if amount <= 0:
        return _err('Amount must be positive.')
    txn_date = _validate_date(d.get('date')) or date.today().isoformat()
    desc = _safe(d.get('description', 'Transfer')).strip() or 'Transfer'
    notes = _safe(d.get('notes', '')).strip()

    db = get_db(current_app)
    from_acct = db.execute('SELECT name FROM accounts WHERE id=?', (from_id,)).fetchone()
    to_acct = db.execute('SELECT name FROM accounts WHERE id=?', (to_id,)).fetchone()
    if not from_acct or not to_acct:
        return _err('Account not found.', 404)

    cur1 = db.execute(
        'INSERT INTO transactions (account_id, date, description, category, amount, notes, tags) VALUES (?,?,?,?,?,?,?)',
        (from_id, txn_date, f"{desc} \u2192 {to_acct['name']}", 'Transfer', -amount, notes, '')
    )
    tid1 = cur1.lastrowid
    cur2 = db.execute(
        'INSERT INTO transactions (account_id, date, description, category, amount, notes, tags, transfer_id) VALUES (?,?,?,?,?,?,?,?)',
        (to_id, txn_date, f"{desc} \u2190 {from_acct['name']}", 'Transfer', amount, notes, '', tid1)
    )
    tid2 = cur2.lastrowid
    db.execute('UPDATE transactions SET transfer_id=? WHERE id=?', (tid2, tid1))
    _reconcile_balance(db, from_id, -amount)
    _reconcile_balance(db, to_id, amount)
    db.commit()
    snapshot_net_worth(current_app)
    return jsonify({'ok': True, 'from_txn_id': tid1, 'to_txn_id': tid2}), 201


# ── Split Transactions ───────────────────────────────────────────────────────

@transactions_bp.route('/api/transactions/<int:tid>/split', methods=['POST'])
@login_required
def api_split_transaction(tid):
    d = request.get_json()
    splits = d.get('splits', [])
    if not splits or len(splits) < 2:
        return _err('At least 2 split items required.')
    db = get_db(current_app)
    parent = db.execute('SELECT * FROM transactions WHERE id=?', (tid,)).fetchone()
    if not parent:
        return _err('Transaction not found.', 404)

    total = sum(round(float(s.get('amount', 0)), 2) for s in splits)
    if round(total, 2) != round(parent['amount'], 2):
        return _err(f'Split amounts ({total}) must equal original ({parent["amount"]}).')

    db.execute('UPDATE transactions SET is_split=1 WHERE id=?', (tid,))

    created = []
    for s in splits:
        amt = round(float(s['amount']), 2)
        cat = _safe(s.get('category', parent['category'])).strip()
        desc = _safe(s.get('description', parent['description'])).strip()
        cur = db.execute(
            'INSERT INTO transactions (account_id, date, description, category, amount, notes, tags, is_split, split_parent_id) VALUES (?,?,?,?,?,?,?,1,?)',
            (parent['account_id'], parent['date'], desc, cat, amt, '', '', tid)
        )
        created.append(cur.lastrowid)
    db.commit()
    return jsonify({'ok': True, 'split_ids': created}), 201


# ── Recurring ────────────────────────────────────────────────────────────────

@transactions_bp.route('/api/recurring')
@login_required
def api_recurring():
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM recurring ORDER BY next_date').fetchall()
    return jsonify([dict(r) for r in rows])


@transactions_bp.route('/api/recurring', methods=['POST'])
@login_required
def api_recurring_create():
    d = request.get_json()
    if not d or not d.get('name', '').strip():
        return _err('Name is required.')
    if d.get('frequency') not in VALID_FREQUENCIES:
        return _err('Invalid frequency.')
    try:
        amount = round(float(d['amount']), 2)
    except (ValueError, TypeError):
        return _err('Invalid amount.')
    db = get_db(current_app)
    cur = db.execute(
        'INSERT INTO recurring (name, amount, frequency, category, next_date, account_id, active) VALUES (?,?,?,?,?,?,?)',
        (_safe(d['name']).strip(), amount, d['frequency'], _safe(d.get('category', '')).strip(),
         _validate_date(d.get('next_date')) or '', d.get('account_id'), int(d.get('active', 1)))
    )
    db.commit()
    row = db.execute('SELECT * FROM recurring WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@transactions_bp.route('/api/recurring/<int:rid>', methods=['PUT'])
@login_required
def api_recurring_update(rid):
    d = request.get_json()
    if not d or not d.get('name', '').strip():
        return _err('Name is required.')
    if d.get('frequency') not in VALID_FREQUENCIES:
        return _err('Invalid frequency.')
    try:
        amount = round(float(d['amount']), 2)
    except (ValueError, TypeError):
        return _err('Invalid amount.')
    db = get_db(current_app)
    db.execute(
        'UPDATE recurring SET name=?, amount=?, frequency=?, category=?, next_date=?, account_id=?, active=? WHERE id=?',
        (_safe(d['name']).strip(), amount, d['frequency'], _safe(d.get('category', '')).strip(),
         _validate_date(d.get('next_date')) or '', d.get('account_id'), int(d.get('active', 1)), rid)
    )
    db.commit()
    row = db.execute('SELECT * FROM recurring WHERE id=?', (rid,)).fetchone()
    if not row:
        return _err('Recurring item not found.', 404)
    return jsonify(dict(row))


@transactions_bp.route('/api/recurring/<int:rid>', methods=['DELETE'])
@login_required
def api_recurring_delete(rid):
    db = get_db(current_app)
    db.execute('DELETE FROM recurring WHERE id=?', (rid,))
    db.commit()
    return jsonify({'ok': True})


@transactions_bp.route('/api/recurring/<int:rid>/toggle', methods=['POST'])
@login_required
def api_recurring_toggle(rid):
    db = get_db(current_app)
    db.execute('UPDATE recurring SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?', (rid,))
    db.commit()
    row = db.execute('SELECT * FROM recurring WHERE id=?', (rid,)).fetchone()
    if not row:
        return _err('Recurring item not found.', 404)
    return jsonify(dict(row))


@transactions_bp.route('/api/recurring/autopost', methods=['POST'])
@login_required
def api_autopost():
    posted = process_recurring(current_app)
    if posted:
        snapshot_net_worth(current_app)
    return jsonify({'ok': True, 'posted': posted})


# ── Auto-categorize ──────────────────────────────────────────────────────────

@transactions_bp.route('/api/autocategorize', methods=['POST'])
@login_required
def api_autocategorize():
    d = request.get_json()
    desc = d.get('description', '') if d else ''
    cat, tags = auto_categorize(current_app, desc)
    return jsonify({'category': cat, 'tags': tags})


# ── Bulk Operations ──────────────────────────────────────────────────────────

@transactions_bp.route('/api/transactions/bulk', methods=['POST'])
@login_required
def api_bulk_transactions():
    d = request.get_json()
    if not d or not d.get('ids'):
        return _err('Transaction IDs required.')
    try:
        ids = [int(i) for i in d['ids']]
    except (ValueError, TypeError):
        return _err('Invalid transaction IDs.')
    action = d.get('action', '')
    db = get_db(current_app)

    if action == 'delete':
        for tid in ids:
            old = db.execute('SELECT account_id, amount FROM transactions WHERE id=?', (tid,)).fetchone()
            if old:
                _reconcile_balance(db, old['account_id'], -old['amount'])
            db.execute('DELETE FROM transactions WHERE id=?', (tid,))
        db.commit()
        snapshot_net_worth(current_app)
        return jsonify({'ok': True, 'affected': len(ids)})

    elif action == 'categorize':
        cat = _safe(d.get('category', '')).strip()
        if not cat:
            return _err('Category is required for categorize action.')
        placeholders = ','.join(['?'] * len(ids))
        db.execute(f'UPDATE transactions SET category=? WHERE id IN ({placeholders})', [cat] + list(ids))
        db.commit()
        return jsonify({'ok': True, 'affected': len(ids)})

    elif action == 'tag':
        new_tag = _safe(d.get('tag', '')).strip()
        if not new_tag:
            return _err('Tag is required.')
        for tid in ids:
            row = db.execute('SELECT tags FROM transactions WHERE id=?', (tid,)).fetchone()
            if row:
                existing = [t.strip() for t in (row['tags'] or '').split(',') if t.strip()]
                if new_tag not in existing:
                    existing.append(new_tag)
                db.execute('UPDATE transactions SET tags=? WHERE id=?', (','.join(existing), tid))
        db.commit()
        return jsonify({'ok': True, 'affected': len(ids)})

    return _err('Invalid action. Use: delete, categorize, tag.')


# ── CSV Export ───────────────────────────────────────────────────────────────

@transactions_bp.route('/api/export/transactions')
@login_required
@_licensed_only
def export_transactions():
    db = get_db(current_app)
    rows = db.execute(
        'SELECT t.date, t.description, t.category, t.amount, t.notes, a.name as account_name '
        'FROM transactions t JOIN accounts a ON t.account_id=a.id ORDER BY t.date DESC'
    ).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Date', 'Description', 'Category', 'Amount', 'Account', 'Notes'])
    for r in rows:
        w.writerow([r['date'], r['description'], r['category'], r['amount'], r['account_name'], r['notes'] or ''])
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=wealthwatch_transactions.csv'})


# ── CSV Import ───────────────────────────────────────────────────────────────

@transactions_bp.route('/api/import/transactions', methods=['POST'])
@login_required
@_licensed_only
def import_transactions():
    f = request.files.get('file')
    if not f:
        return _err('No file uploaded.')
    account_id = request.form.get('account_id')
    if not account_id:
        return _err('Account ID is required.')
    try:
        account_id = int(account_id)
    except ValueError:
        return _err('Invalid account ID.')

    db = get_db(current_app)
    if not db.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone():
        return _err('Account not found.', 404)

    try:
        content = f.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return _err('File must be UTF-8 encoded CSV.')

    reader = csv.DictReader(io.StringIO(content))
    imported = 0
    balance_delta = 0.0
    for row in reader:
        txn_date = _validate_date(row.get('Date', row.get('date', '')))
        desc = _safe(row.get('Description', row.get('description', ''))).strip()
        cat = _safe(row.get('Category', row.get('category', 'Uncategorized'))).strip()
        notes = _safe(row.get('Notes', row.get('notes', ''))).strip()
        try:
            amount = round(float(row.get('Amount', row.get('amount', 0))), 2)
        except (ValueError, TypeError):
            continue
        if not txn_date or not desc:
            continue
        db.execute(
            'INSERT INTO transactions (account_id, date, description, category, amount, notes) VALUES (?,?,?,?,?,?)',
            (account_id, txn_date, desc, cat or 'Uncategorized', amount, notes)
        )
        balance_delta += amount
        imported += 1

    if balance_delta != 0:
        _reconcile_balance(db, account_id, balance_delta)
    db.commit()
    snapshot_net_worth(current_app)
    return jsonify({'ok': True, 'imported': imported})


# ── Investment Holdings CRUD ─────────────────────────────────────────────────

@transactions_bp.route('/api/holdings')
@login_required
def api_holdings():
    db = get_db(current_app)
    rows = db.execute(
        'SELECT h.*, a.name as account_name FROM holdings h '
        'JOIN accounts a ON h.account_id=a.id ORDER BY h.account_id, h.symbol'
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['market_value'] = round(d['shares'] * d['current_price'], 2)
        d['gain_loss'] = round(d['market_value'] - d['cost_basis'], 2)
        d['gain_pct'] = round((d['gain_loss'] / d['cost_basis']) * 100, 2) if d['cost_basis'] else 0
        result.append(d)
    return jsonify(result)


@transactions_bp.route('/api/holdings', methods=['POST'])
@login_required
@_demo_check_holdings
def api_holdings_create():
    d = request.get_json()
    if not d or not d.get('symbol', '').strip() or not d.get('name', '').strip():
        return _err('Symbol and name are required.')
    try:
        shares = round(float(d.get('shares', 0)), 4)
        cost_basis = round(float(d.get('cost_basis', 0)), 2)
        price = round(float(d.get('current_price', 0)), 2)
        account_id = int(d['account_id'])
    except (ValueError, TypeError, KeyError):
        return _err('Invalid data.')
    db = get_db(current_app)
    cur = db.execute(
        'INSERT INTO holdings (account_id, symbol, name, shares, cost_basis, current_price, asset_class) VALUES (?,?,?,?,?,?,?)',
        (account_id, _safe(d['symbol']).strip().upper(), _safe(d['name']).strip(),
         shares, cost_basis, price, d.get('asset_class', 'stock'))
    )
    db.commit()
    row = db.execute('SELECT h.*, a.name as account_name FROM holdings h JOIN accounts a ON h.account_id=a.id WHERE h.id=?', (cur.lastrowid,)).fetchone()
    dr = dict(row)
    dr['market_value'] = round(dr['shares'] * dr['current_price'], 2)
    dr['gain_loss'] = round(dr['market_value'] - dr['cost_basis'], 2)
    dr['gain_pct'] = round((dr['gain_loss'] / dr['cost_basis']) * 100, 2) if dr['cost_basis'] else 0
    return jsonify(dr), 201


@transactions_bp.route('/api/holdings/<int:hid>', methods=['PUT'])
@login_required
def api_holdings_update(hid):
    d = request.get_json()
    if not d or not d.get('symbol', '').strip():
        return _err('Symbol is required.')
    try:
        shares = round(float(d.get('shares', 0)), 4)
        cost_basis = round(float(d.get('cost_basis', 0)), 2)
        price = round(float(d.get('current_price', 0)), 2)
    except (ValueError, TypeError):
        return _err('Invalid data.')
    db = get_db(current_app)
    db.execute(
        'UPDATE holdings SET symbol=?, name=?, shares=?, cost_basis=?, current_price=?, asset_class=?, last_updated=? WHERE id=?',
        (_safe(d['symbol']).strip().upper(), _safe(d.get('name', '')).strip(),
         shares, cost_basis, price, d.get('asset_class', 'stock'), date.today().isoformat(), hid)
    )
    db.commit()
    row = db.execute('SELECT h.*, a.name as account_name FROM holdings h JOIN accounts a ON h.account_id=a.id WHERE h.id=?', (hid,)).fetchone()
    if not row:
        return _err('Holding not found.', 404)
    dr = dict(row)
    dr['market_value'] = round(dr['shares'] * dr['current_price'], 2)
    dr['gain_loss'] = round(dr['market_value'] - dr['cost_basis'], 2)
    dr['gain_pct'] = round((dr['gain_loss'] / dr['cost_basis']) * 100, 2) if dr['cost_basis'] else 0
    return jsonify(dr)


@transactions_bp.route('/api/holdings/<int:hid>', methods=['DELETE'])
@login_required
def api_holdings_delete(hid):
    db = get_db(current_app)
    db.execute('DELETE FROM holdings WHERE id=?', (hid,))
    db.commit()
    return jsonify({'ok': True})


# ── Receipt Upload ───────────────────────────────────────────────────────────

@transactions_bp.route('/api/transactions/<int:tid>/receipt', methods=['POST'])
@login_required
def api_upload_receipt(tid):
    f = request.files.get('file')
    if not f or not f.filename:
        return _err('No file uploaded.')
    db = get_db(current_app)
    if not db.execute('SELECT 1 FROM transactions WHERE id=?', (tid,)).fetchone():
        return _err('Transaction not found.', 404)
    upload_dir = os.path.join(current_app.instance_path, 'receipts')
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.pdf', '.webp'):
        return _err('Invalid file type. Allowed: jpg, png, gif, pdf, webp.')
    safe_name = f'{tid}_{secrets.token_hex(8)}{ext}'
    f.save(os.path.join(upload_dir, safe_name))
    db.execute(
        'INSERT INTO receipts (transaction_id, filename, original_name) VALUES (?,?,?)',
        (tid, safe_name, _safe(f.filename))
    )
    db.commit()
    return jsonify({'ok': True, 'filename': safe_name}), 201


@transactions_bp.route('/api/transactions/<int:tid>/receipts')
@login_required
def api_get_receipts(tid):
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM receipts WHERE transaction_id=? ORDER BY uploaded_at DESC', (tid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@transactions_bp.route('/receipts/<filename>')
@login_required
def serve_receipt(filename):
    return send_from_directory(os.path.join(current_app.instance_path, 'receipts'), filename)
