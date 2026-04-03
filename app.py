from flask import Flask, render_template, jsonify, request, redirect, url_for, session, Response
from database import init_db, get_db, snapshot_net_worth, process_recurring, auto_categorize, save_rollover
from licensing import get_license_status, activate_license, deactivate_license, data_dir
from functools import wraps
from markupsafe import escape
import os, secrets, csv, io, re
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


def create_app():
    app = Flask(__name__)

    # Store data in user's AppData (persists across exe updates)
    app_data = data_dir()
    app.config['DATABASE'] = os.path.join(app_data, 'wealthwatch.db')
    app.config['SECRET_KEY'] = os.environ.get('WW_SECRET_KEY', secrets.token_hex(32))
    app.instance_path = app_data

    os.makedirs(app_data, exist_ok=True)

    with app.app_context():
        init_db(app)

    # ── Auth config ──────────────────────────────────────────────────────────
    pin_file = os.path.join(app_data, '.pin')
    if os.path.exists(pin_file):
        with open(pin_file) as _pf:
            DEFAULT_PIN = _pf.read().strip()
    else:
        DEFAULT_PIN = secrets.token_hex(3)[:6]  # random 6-digit PIN on first run
        with open(pin_file, 'w') as _pf:
            _pf.write(DEFAULT_PIN)
        print(f'\n  *** First-run PIN generated: {DEFAULT_PIN} ***\n  Stored in {pin_file}\n')

    def login_required(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get('authed'):
                if request.path.startswith('/api/'):
                    return _err('Unauthorized', 401)
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return wrapped

    # ── CSRF ─────────────────────────────────────────────────────────────────
    @app.before_request
    def csrf_protect():
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        if request.path == '/login':
            return
        if request.path.startswith('/api/'):
            return  # API routes protected by session auth + same-origin policy
        # form posts need CSRF token
        token = request.form.get('_csrf') or request.headers.get('X-CSRF-Token')
        if token != session.get('csrf_token'):
            return _err('CSRF token invalid', 403)

    @app.context_processor
    def inject_csrf():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        return {'csrf_token': session['csrf_token']}

    # ── License enforcement ──────────────────────────────────────────────────
    def demo_check(resource, count_sql=None):
        """Decorator to enforce demo limits on create endpoints."""
        def decorator(f):
            @wraps(f)
            def wrapped(*args, **kwargs):
                lic = get_license_status()
                if lic['licensed']:
                    return f(*args, **kwargs)
                if lic['expired']:
                    return _err('Trial expired. Please activate a license to continue.', 403)
                if count_sql and lic['limits']:
                    limit_key = f'max_{resource}'
                    max_val = lic['limits'].get(limit_key)
                    if max_val is not None:
                        db = get_db(app)
                        current = db.execute(count_sql).fetchone()[0]
                        if current >= max_val:
                            return _err(f'Demo limit reached: max {max_val} {resource}. Activate a license for unlimited access.', 403)
                return f(*args, **kwargs)
            return wrapped
        return decorator

    def licensed_only(f):
        """Block feature entirely in demo mode."""
        @wraps(f)
        def wrapped(*args, **kwargs):
            lic = get_license_status()
            if not lic['licensed']:
                feature = 'This feature'
                return _err(f'{feature} requires a license. Activate to unlock.', 403)
            return f(*args, **kwargs)
        return wrapped

    @app.context_processor
    def inject_globals():
        profile_file = os.path.join(app_data, '.profile')
        user_name = 'User'
        if os.path.exists(profile_file):
            import json as _j
            try:
                with open(profile_file) as _f:
                    user_name = _j.load(_f).get('name', 'User')
            except Exception:
                pass
        return {'license': get_license_status(), 'user_name': user_name}

    @app.route('/api/profile', methods=['GET', 'POST'])
    @login_required
    def api_profile():
        import json as jsonlib
        profile_file = os.path.join(app_data, '.profile')
        if request.method == 'POST':
            d = request.get_json()
            with open(profile_file, 'w') as f:
                jsonlib.dump({'name': _safe(d.get('name', 'User')).strip()}, f)
            return jsonify({'ok': True})
        if os.path.exists(profile_file):
            with open(profile_file) as f:
                return jsonify(jsonlib.load(f))
        return jsonify({'name': 'User'})

    # ── License routes ────────────────────────────────────────────────────────
    @app.route('/api/license')
    @login_required
    def api_license_status():
        return jsonify(get_license_status())

    @app.route('/api/license/activate', methods=['POST'])
    @login_required
    def api_license_activate():
        d = request.get_json()
        key = d.get('key', '') if d else ''
        success, msg = activate_license(key)
        if success:
            return jsonify({'ok': True, 'message': msg})
        return _err(msg)

    @app.route('/api/license/deactivate', methods=['POST'])
    @login_required
    def api_license_deactivate():
        deactivate_license()
        return jsonify({'ok': True})

    # ── Error pages ──────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api/'):
            return _err('Not found', 404)
        return render_template('error.html', code=404, message='Page not found'), 404

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith('/api/'):
            return _err('Internal server error', 500)
        return render_template('error.html', code=500, message='Something went wrong'), 500

    # ── Auth routes ──────────────────────────────────────────────────────────
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            pin = request.form.get('pin', '').strip()
            if pin == DEFAULT_PIN:
                session['authed'] = True
                session.permanent = True
                return redirect(url_for('dashboard'))
            error = 'Invalid PIN. Try again.'
        return render_template('login.html', error=error)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    # ── Page routes ──────────────────────────────────────────────────────────
    @app.route('/')
    @app.route('/dashboard')
    @login_required
    def dashboard():
        return render_template('dashboard.html', active='dashboard')

    @app.route('/accounts')
    @login_required
    def accounts():
        return render_template('accounts.html', active='accounts')

    @app.route('/transactions')
    @login_required
    def transactions():
        return render_template('transactions.html', active='transactions')

    @app.route('/cashflow')
    @login_required
    def cashflow():
        return render_template('cashflow.html', active='cashflow')

    @app.route('/budget')
    @login_required
    def budget():
        return render_template('budget.html', active='budget')

    @app.route('/goals')
    @login_required
    def goals():
        return render_template('goals.html', active='goals')

    @app.route('/recurring')
    @login_required
    def recurring():
        return render_template('recurring.html', active='recurring')

    @app.route('/reports')
    @login_required
    def reports():
        return render_template('reports.html', active='reports')

    @app.route('/rules')
    @login_required
    def rules():
        return render_template('rules.html', active='rules')

    @app.route('/investments')
    @login_required
    def investments():
        return render_template('investments.html', active='investments')

    # ── API: Read ─────────────────────────────────────────────────────────────
    @app.route('/api/accounts')
    @login_required
    def api_accounts():
        db = get_db(app)
        rows = db.execute('SELECT * FROM accounts ORDER BY type, name').fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route('/api/transactions')
    @login_required
    def api_transactions():
        db = get_db(app)
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

    @app.route('/api/budget')
    @login_required
    def api_budget():
        db = get_db(app)
        # Auto-calculate spent from current month transactions
        rows = db.execute('SELECT * FROM budget_categories ORDER BY category').fetchall()
        month = date.today().strftime('%Y-%m')
        spent_rows = db.execute(
            "SELECT category, SUM(ABS(amount)) as spent FROM transactions "
            "WHERE amount < 0 AND strftime('%Y-%m', date) = ? GROUP BY category",
            (month,)
        ).fetchall()
        spent_map = {r['category']: round(r['spent'], 2) for r in spent_rows}
        rollover_rows = db.execute(
            'SELECT category, rollover FROM budget_rollover WHERE month=?', (month,)
        ).fetchall()
        rollover_map = {r['category']: round(r['rollover'], 2) for r in rollover_rows}
        result = []
        for r in rows:
            d = dict(r)
            d['spent'] = spent_map.get(d['category'], 0.0)
            d['rollover'] = rollover_map.get(d['category'], 0.0)
            d['effective_budget'] = round(d['budgeted'] + d['rollover'], 2)
            result.append(d)
        return jsonify(result)

    @app.route('/api/goals')
    @login_required
    def api_goals():
        db = get_db(app)
        rows = db.execute('SELECT * FROM goals ORDER BY target_date').fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route('/api/recurring')
    @login_required
    def api_recurring():
        db = get_db(app)
        rows = db.execute('SELECT * FROM recurring ORDER BY next_date').fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route('/api/cashflow')
    @login_required
    def api_cashflow():
        db = get_db(app)
        rows = db.execute(
            'SELECT strftime("%Y-%m", date) as month, '
            'SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as income, '
            'SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) as expenses '
            'FROM transactions GROUP BY month ORDER BY month DESC LIMIT 12'
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route('/api/summary')
    @login_required
    def api_summary():
        db = get_db(app)
        net_worth = db.execute('SELECT SUM(balance) FROM accounts').fetchone()[0] or 0
        cash = db.execute("SELECT SUM(balance) FROM accounts WHERE type='checking' OR type='savings'").fetchone()[0] or 0
        investments = db.execute("SELECT SUM(balance) FROM accounts WHERE type='investment'").fetchone()[0] or 0
        income = db.execute(
            "SELECT SUM(amount) FROM transactions WHERE amount > 0 AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now')"
        ).fetchone()[0] or 0
        expenses = db.execute(
            "SELECT SUM(ABS(amount)) FROM transactions WHERE amount < 0 AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now')"
        ).fetchone()[0] or 0
        return jsonify({
            'net_worth': round(net_worth, 2),
            'cash': round(cash, 2),
            'investments': round(investments, 2),
            'monthly_income': round(income, 2),
            'monthly_expenses': round(expenses, 2),
            'monthly_savings': round(income - expenses, 2)
        })

    @app.route('/api/networth')
    @login_required
    def api_networth():
        db = get_db(app)
        rows = db.execute('SELECT * FROM net_worth_history ORDER BY date ASC').fetchall()
        return jsonify([dict(r) for r in rows])

    # ── CRUD: Accounts ────────────────────────────────────────────────────────
    @app.route('/api/accounts', methods=['POST'])
    @login_required
    @demo_check('accounts', 'SELECT COUNT(*) FROM accounts')
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
        db = get_db(app)
        cur = db.execute(
            'INSERT INTO accounts (name, institution, type, balance, last_updated) VALUES (?,?,?,?,?)',
            (_safe(d['name']).strip(), _safe(d.get('institution', '')).strip(),
             d['type'], balance, date.today().isoformat())
        )
        db.commit()
        snapshot_net_worth(app)
        row = db.execute('SELECT * FROM accounts WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.route('/api/accounts/<int:aid>', methods=['PUT'])
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
        db = get_db(app)
        db.execute(
            'UPDATE accounts SET name=?, institution=?, type=?, balance=?, last_updated=? WHERE id=?',
            (_safe(d['name']).strip(), _safe(d.get('institution', '')).strip(),
             d['type'], balance, date.today().isoformat(), aid)
        )
        db.commit()
        snapshot_net_worth(app)
        row = db.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        if not row:
            return _err('Account not found.', 404)
        return jsonify(dict(row))

    @app.route('/api/accounts/<int:aid>', methods=['DELETE'])
    @login_required
    def api_accounts_delete(aid):
        db = get_db(app)
        db.execute('DELETE FROM accounts WHERE id=?', (aid,))
        db.commit()
        snapshot_net_worth(app)
        return jsonify({'ok': True})

    # ── CRUD: Transactions ────────────────────────────────────────────────────
    def _reconcile_balance(db, account_id, amount_delta):
        """Adjust account balance by amount_delta."""
        db.execute('UPDATE accounts SET balance = balance + ?, last_updated = ? WHERE id = ?',
                   (round(amount_delta, 2), date.today().isoformat(), account_id))

    @app.route('/api/transactions', methods=['POST'])
    @login_required
    @demo_check('transactions', 'SELECT COUNT(*) FROM transactions')
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
        db = get_db(app)
        # Verify account exists
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
        snapshot_net_worth(app)
        row = db.execute(
            'SELECT t.*, a.name as account_name FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.id=?',
            (cur.lastrowid,)
        ).fetchone()
        return jsonify(dict(row)), 201

    @app.route('/api/transactions/<int:tid>', methods=['PUT'])
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
        db = get_db(app)
        old = db.execute('SELECT account_id, amount FROM transactions WHERE id=?', (tid,)).fetchone()
        if not old:
            return _err('Transaction not found.', 404)
        # Reverse old amount on old account, apply new amount on new account
        _reconcile_balance(db, old['account_id'], -old['amount'])
        _reconcile_balance(db, new_account_id, new_amount)
        tags = ','.join([_safe(t).strip() for t in (d.get('tags') or '').split(',') if t.strip()])
        db.execute(
            'UPDATE transactions SET account_id=?, date=?, description=?, category=?, amount=?, notes=?, tags=? WHERE id=?',
            (new_account_id, txn_date, _safe(d['description']).strip(), _safe(d['category']).strip(),
             new_amount, _safe(d.get('notes', '')).strip(), tags, tid)
        )
        db.commit()
        snapshot_net_worth(app)
        row = db.execute(
            'SELECT t.*, a.name as account_name FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.id=?',
            (tid,)
        ).fetchone()
        return jsonify(dict(row))

    @app.route('/api/transactions/<int:tid>', methods=['DELETE'])
    @login_required
    def api_transactions_delete(tid):
        db = get_db(app)
        old = db.execute('SELECT account_id, amount FROM transactions WHERE id=?', (tid,)).fetchone()
        if old:
            _reconcile_balance(db, old['account_id'], -old['amount'])
        db.execute('DELETE FROM transactions WHERE id=?', (tid,))
        db.commit()
        snapshot_net_worth(app)
        return jsonify({'ok': True})

    # ── CRUD: Budget Categories ───────────────────────────────────────────────
    @app.route('/api/budget', methods=['POST'])
    @login_required
    def api_budget_create():
        d = request.get_json()
        if not d or not d.get('category', '').strip():
            return _err('Category name is required.')
        try:
            budgeted = round(float(d.get('budgeted', 0)), 2)
        except (ValueError, TypeError):
            return _err('Invalid budget amount.')
        db = get_db(app)
        try:
            cur = db.execute(
                'INSERT INTO budget_categories (category, budgeted, icon) VALUES (?,?,?)',
                (_safe(d['category']).strip(), budgeted, _safe(d.get('icon', '💰')).strip())
            )
        except db.IntegrityError:
            return _err('Category already exists.')
        db.commit()
        row = db.execute('SELECT * FROM budget_categories WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.route('/api/budget/<int:bid>', methods=['PUT'])
    @login_required
    def api_budget_update(bid):
        d = request.get_json()
        if not d or not d.get('category', '').strip():
            return _err('Category name is required.')
        try:
            budgeted = round(float(d.get('budgeted', 0)), 2)
        except (ValueError, TypeError):
            return _err('Invalid budget amount.')
        db = get_db(app)
        db.execute(
            'UPDATE budget_categories SET category=?, budgeted=?, icon=? WHERE id=?',
            (_safe(d['category']).strip(), budgeted, _safe(d.get('icon', '💰')).strip(), bid)
        )
        db.commit()
        row = db.execute('SELECT * FROM budget_categories WHERE id=?', (bid,)).fetchone()
        if not row:
            return _err('Budget category not found.', 404)
        return jsonify(dict(row))

    @app.route('/api/budget/<int:bid>', methods=['DELETE'])
    @login_required
    def api_budget_delete(bid):
        db = get_db(app)
        db.execute('DELETE FROM budget_categories WHERE id=?', (bid,))
        db.commit()
        return jsonify({'ok': True})

    # ── CRUD: Goals ───────────────────────────────────────────────────────────
    @app.route('/api/goals', methods=['POST'])
    @login_required
    def api_goals_create():
        d = request.get_json()
        if not d or not d.get('name', '').strip():
            return _err('Goal name is required.')
        try:
            target = round(float(d['target_amount']), 2)
            current = round(float(d.get('current_amount', 0)), 2)
        except (ValueError, TypeError, KeyError):
            return _err('Invalid amount.')
        db = get_db(app)
        cur = db.execute(
            'INSERT INTO goals (name, target_amount, current_amount, target_date, icon, color) VALUES (?,?,?,?,?,?)',
            (_safe(d['name']).strip(), target, current,
             _validate_date(d.get('target_date')) or '', _safe(d.get('icon', '🎯')), d.get('color', '#4CAF50'))
        )
        db.commit()
        row = db.execute('SELECT * FROM goals WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.route('/api/goals/<int:gid>', methods=['PUT'])
    @login_required
    def api_goals_update(gid):
        d = request.get_json()
        if not d or not d.get('name', '').strip():
            return _err('Goal name is required.')
        try:
            target = round(float(d['target_amount']), 2)
            current = round(float(d.get('current_amount', 0)), 2)
        except (ValueError, TypeError, KeyError):
            return _err('Invalid amount.')
        db = get_db(app)
        db.execute(
            'UPDATE goals SET name=?, target_amount=?, current_amount=?, target_date=?, icon=?, color=? WHERE id=?',
            (_safe(d['name']).strip(), target, current,
             _validate_date(d.get('target_date')) or '', _safe(d.get('icon', '🎯')), d.get('color', '#4CAF50'), gid)
        )
        db.commit()
        row = db.execute('SELECT * FROM goals WHERE id=?', (gid,)).fetchone()
        if not row:
            return _err('Goal not found.', 404)
        return jsonify(dict(row))

    @app.route('/api/goals/<int:gid>', methods=['DELETE'])
    @login_required
    def api_goals_delete(gid):
        db = get_db(app)
        db.execute('DELETE FROM goals WHERE id=?', (gid,))
        db.commit()
        return jsonify({'ok': True})

    # ── CRUD: Recurring ───────────────────────────────────────────────────────
    @app.route('/api/recurring', methods=['POST'])
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
        db = get_db(app)
        cur = db.execute(
            'INSERT INTO recurring (name, amount, frequency, category, next_date, account_id, active) VALUES (?,?,?,?,?,?,?)',
            (_safe(d['name']).strip(), amount, d['frequency'], _safe(d.get('category', '')).strip(),
             _validate_date(d.get('next_date')) or '', d.get('account_id'), int(d.get('active', 1)))
        )
        db.commit()
        row = db.execute('SELECT * FROM recurring WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.route('/api/recurring/<int:rid>', methods=['PUT'])
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
        db = get_db(app)
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

    @app.route('/api/recurring/<int:rid>', methods=['DELETE'])
    @login_required
    def api_recurring_delete(rid):
        db = get_db(app)
        db.execute('DELETE FROM recurring WHERE id=?', (rid,))
        db.commit()
        return jsonify({'ok': True})

    @app.route('/api/recurring/<int:rid>/toggle', methods=['POST'])
    @login_required
    def api_recurring_toggle(rid):
        db = get_db(app)
        db.execute('UPDATE recurring SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?', (rid,))
        db.commit()
        row = db.execute('SELECT * FROM recurring WHERE id=?', (rid,)).fetchone()
        if not row:
            return _err('Recurring item not found.', 404)
        return jsonify(dict(row))

    # ── Transfers ─────────────────────────────────────────────────────────────
    @app.route('/api/transfers', methods=['POST'])
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

        db = get_db(app)
        from_acct = db.execute('SELECT name FROM accounts WHERE id=?', (from_id,)).fetchone()
        to_acct = db.execute('SELECT name FROM accounts WHERE id=?', (to_id,)).fetchone()
        if not from_acct or not to_acct:
            return _err('Account not found.', 404)

        # Create paired transactions
        cur1 = db.execute(
            'INSERT INTO transactions (account_id, date, description, category, amount, notes, tags) VALUES (?,?,?,?,?,?,?)',
            (from_id, txn_date, f"{desc} → {to_acct['name']}", 'Transfer', -amount, notes, '')
        )
        tid1 = cur1.lastrowid
        cur2 = db.execute(
            'INSERT INTO transactions (account_id, date, description, category, amount, notes, tags, transfer_id) VALUES (?,?,?,?,?,?,?,?)',
            (to_id, txn_date, f"{desc} ← {from_acct['name']}", 'Transfer', amount, notes, '', tid1)
        )
        tid2 = cur2.lastrowid
        db.execute('UPDATE transactions SET transfer_id=? WHERE id=?', (tid2, tid1))
        _reconcile_balance(db, from_id, -amount)
        _reconcile_balance(db, to_id, amount)
        db.commit()
        snapshot_net_worth(app)
        return jsonify({'ok': True, 'from_txn_id': tid1, 'to_txn_id': tid2}), 201

    # ── Split Transactions ────────────────────────────────────────────────────
    @app.route('/api/transactions/<int:tid>/split', methods=['POST'])
    @login_required
    def api_split_transaction(tid):
        d = request.get_json()
        splits = d.get('splits', [])
        if not splits or len(splits) < 2:
            return _err('At least 2 split items required.')
        db = get_db(app)
        parent = db.execute('SELECT * FROM transactions WHERE id=?', (tid,)).fetchone()
        if not parent:
            return _err('Transaction not found.', 404)

        total = sum(round(float(s.get('amount', 0)), 2) for s in splits)
        if round(total, 2) != round(parent['amount'], 2):
            return _err(f'Split amounts ({total}) must equal original ({parent["amount"]}).')

        # Mark parent as split
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

    # ── Recurring Auto-Post ───────────────────────────────────────────────────
    @app.route('/api/recurring/autopost', methods=['POST'])
    @login_required
    def api_autopost():
        posted = process_recurring(app)
        if posted:
            snapshot_net_worth(app)
        return jsonify({'ok': True, 'posted': posted})

    # ── Transaction Rules CRUD ────────────────────────────────────────────────
    @app.route('/api/rules')
    @login_required
    def api_rules():
        db = get_db(app)
        rows = db.execute('SELECT * FROM transaction_rules ORDER BY priority DESC, pattern').fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route('/api/rules', methods=['POST'])
    @login_required
    def api_rules_create():
        d = request.get_json()
        if not d or not d.get('pattern', '').strip() or not d.get('category', '').strip():
            return _err('Pattern and category are required.')
        db = get_db(app)
        cur = db.execute(
            'INSERT INTO transaction_rules (pattern, category, tags, priority) VALUES (?,?,?,?)',
            (_safe(d['pattern']).strip(), _safe(d['category']).strip(),
             _safe(d.get('tags', '')).strip(), int(d.get('priority', 0)))
        )
        db.commit()
        row = db.execute('SELECT * FROM transaction_rules WHERE id=?', (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.route('/api/rules/<int:rid>', methods=['PUT'])
    @login_required
    def api_rules_update(rid):
        d = request.get_json()
        if not d or not d.get('pattern', '').strip() or not d.get('category', '').strip():
            return _err('Pattern and category are required.')
        db = get_db(app)
        db.execute(
            'UPDATE transaction_rules SET pattern=?, category=?, tags=?, priority=? WHERE id=?',
            (_safe(d['pattern']).strip(), _safe(d['category']).strip(),
             _safe(d.get('tags', '')).strip(), int(d.get('priority', 0)), rid)
        )
        db.commit()
        row = db.execute('SELECT * FROM transaction_rules WHERE id=?', (rid,)).fetchone()
        if not row:
            return _err('Rule not found.', 404)
        return jsonify(dict(row))

    @app.route('/api/rules/<int:rid>', methods=['DELETE'])
    @login_required
    def api_rules_delete(rid):
        db = get_db(app)
        db.execute('DELETE FROM transaction_rules WHERE id=?', (rid,))
        db.commit()
        return jsonify({'ok': True})

    @app.route('/api/autocategorize', methods=['POST'])
    @login_required
    def api_autocategorize():
        d = request.get_json()
        desc = d.get('description', '') if d else ''
        cat, tags = auto_categorize(app, desc)
        return jsonify({'category': cat, 'tags': tags})

    # ── Bulk Operations ───────────────────────────────────────────────────────
    @app.route('/api/transactions/bulk', methods=['POST'])
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
        db = get_db(app)

        if action == 'delete':
            for tid in ids:
                old = db.execute('SELECT account_id, amount FROM transactions WHERE id=?', (tid,)).fetchone()
                if old:
                    _reconcile_balance(db, old['account_id'], -old['amount'])
                db.execute('DELETE FROM transactions WHERE id=?', (tid,))
            db.commit()
            snapshot_net_worth(app)
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

    # ── Alerts / Notifications ────────────────────────────────────────────────
    @app.route('/api/alerts')
    @login_required
    def api_alerts():
        db = get_db(app)
        alerts = []
        month = date.today().strftime('%Y-%m')

        # Over-budget categories
        cats = db.execute('SELECT * FROM budget_categories').fetchall()
        spent_rows = db.execute(
            "SELECT category, SUM(ABS(amount)) as spent FROM transactions "
            "WHERE amount < 0 AND strftime('%Y-%m', date) = ? GROUP BY category",
            (month,)
        ).fetchall()
        spent_map = {r['category']: r['spent'] for r in spent_rows}
        rollover_rows = db.execute(
            'SELECT category, rollover FROM budget_rollover WHERE month=?', (month,)
        ).fetchall()
        rollover_map = {r['category']: r['rollover'] for r in rollover_rows}
        for c in cats:
            spent = spent_map.get(c['category'], 0)
            effective = c['budgeted'] + rollover_map.get(c['category'], 0)
            if effective > 0 and spent > effective:
                over = round(spent - effective, 2)
                alerts.append({
                    'type': 'over_budget', 'severity': 'warning',
                    'message': f"{c['icon']} {c['category']} is ${over:,.2f} over budget"
                })
            elif effective > 0 and spent > effective * 0.9:
                alerts.append({
                    'type': 'near_budget', 'severity': 'info',
                    'message': f"{c['icon']} {c['category']} is at {round(spent/effective*100)}% of budget"
                })

        # Large transactions (> $500) in past 7 days
        week_ago = (date.today() - __import__('datetime').timedelta(days=7)).isoformat()
        large = db.execute(
            'SELECT description, amount, date FROM transactions WHERE ABS(amount) > 500 AND date >= ? ORDER BY date DESC LIMIT 5',
            (week_ago,)
        ).fetchall()
        for t in large:
            alerts.append({
                'type': 'large_transaction', 'severity': 'info',
                'message': f"Large transaction: {t['description']} — ${abs(t['amount']):,.2f} on {t['date']}"
            })

        # Overdue recurring bills
        today_str = date.today().isoformat()
        overdue = db.execute(
            'SELECT name, next_date FROM recurring WHERE active=1 AND next_date != "" AND next_date < ?',
            (today_str,)
        ).fetchall()
        for r in overdue:
            alerts.append({
                'type': 'overdue_bill', 'severity': 'warning',
                'message': f"Overdue bill: {r['name']} was due {r['next_date']}"
            })

        return jsonify(alerts)

    # ── Budget Rollover ───────────────────────────────────────────────────────
    @app.route('/api/budget/rollover', methods=['POST'])
    @login_required
    def api_budget_rollover():
        save_rollover(app)
        return jsonify({'ok': True})

    # ── Spending Trends ───────────────────────────────────────────────────────
    @app.route('/api/spending-trends')
    @login_required
    def api_spending_trends():
        db = get_db(app)
        months_back = request.args.get('months', 6, type=int)
        rows = db.execute(
            "SELECT strftime('%Y-%m', date) as month, category, SUM(ABS(amount)) as spent "
            "FROM transactions WHERE amount < 0 "
            "GROUP BY month, category ORDER BY month DESC"
        ).fetchall()
        # Build { month: { category: spent } }
        data = {}
        all_cats = set()
        for r in rows:
            m = r['month']
            if m not in data:
                data[m] = {}
            data[m][r['category']] = round(r['spent'], 2)
            all_cats.add(r['category'])

        months = sorted(data.keys(), reverse=True)[:months_back]
        months.reverse()  # chronological
        return jsonify({
            'months': months,
            'categories': sorted(all_cats),
            'data': {m: data.get(m, {}) for m in months}
        })

    # ── CSV Export ────────────────────────────────────────────────────────────
    @app.route('/api/export/transactions')
    @login_required
    @licensed_only
    def export_transactions():
        db = get_db(app)
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

    # ── CSV Import ────────────────────────────────────────────────────────────
    @app.route('/api/import/transactions', methods=['POST'])
    @login_required
    @licensed_only
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

        db = get_db(app)
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
        snapshot_net_worth(app)
        return jsonify({'ok': True, 'imported': imported})

    # ── Investment Holdings CRUD ─────────────────────────────────────────────
    @app.route('/api/holdings')
    @login_required
    def api_holdings():
        db = get_db(app)
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

    @app.route('/api/holdings', methods=['POST'])
    @login_required
    @demo_check('holdings', 'SELECT COUNT(*) FROM holdings')
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
        db = get_db(app)
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

    @app.route('/api/holdings/<int:hid>', methods=['PUT'])
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
        db = get_db(app)
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

    @app.route('/api/holdings/<int:hid>', methods=['DELETE'])
    @login_required
    def api_holdings_delete(hid):
        db = get_db(app)
        db.execute('DELETE FROM holdings WHERE id=?', (hid,))
        db.commit()
        return jsonify({'ok': True})

    # ── Receipt Upload ────────────────────────────────────────────────────────
    @app.route('/api/transactions/<int:tid>/receipt', methods=['POST'])
    @login_required
    def api_upload_receipt(tid):
        f = request.files.get('file')
        if not f or not f.filename:
            return _err('No file uploaded.')
        db = get_db(app)
        if not db.execute('SELECT 1 FROM transactions WHERE id=?', (tid,)).fetchone():
            return _err('Transaction not found.', 404)
        upload_dir = os.path.join(app.instance_path, 'receipts')
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

    @app.route('/api/transactions/<int:tid>/receipts')
    @login_required
    def api_get_receipts(tid):
        db = get_db(app)
        rows = db.execute('SELECT * FROM receipts WHERE transaction_id=? ORDER BY uploaded_at DESC', (tid,)).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route('/receipts/<filename>')
    @login_required
    def serve_receipt(filename):
        from flask import send_from_directory
        return send_from_directory(os.path.join(app.instance_path, 'receipts'), filename)

    # ── Data Backup / Restore ─────────────────────────────────────────────────
    @app.route('/api/backup')
    @login_required
    @licensed_only
    def api_backup():
        import json as jsonlib
        db = get_db(app)
        tables = ['accounts', 'transactions', 'budget_categories', 'goals', 'recurring',
                   'net_worth_history', 'transaction_rules', 'budget_rollover', 'holdings']
        backup = {}
        for table in tables:
            try:
                rows = db.execute(f'SELECT * FROM {table}').fetchall()
                backup[table] = [dict(r) for r in rows]
            except Exception:
                backup[table] = []
        data = jsonlib.dumps(backup, indent=2)
        return Response(data, mimetype='application/json',
                        headers={'Content-Disposition': 'attachment; filename=wealthwatch_backup.json'})

    @app.route('/api/restore', methods=['POST'])
    @login_required
    @licensed_only
    def api_restore():
        import json as jsonlib
        f = request.files.get('file')
        if not f:
            return _err('No file uploaded.')
        try:
            data = jsonlib.loads(f.read().decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError):
            return _err('Invalid JSON file.')
        db = get_db(app)
        restored = {}
        table_cols = {
            'accounts': 'name, institution, type, balance, last_updated',
            'budget_categories': 'category, budgeted, icon',
            'goals': 'name, target_amount, current_amount, target_date, icon, color',
            'holdings': 'account_id, symbol, name, shares, cost_basis, current_price, asset_class',
        }
        for table, cols in table_cols.items():
            if table in data and data[table]:
                col_list = [c.strip() for c in cols.split(',')]
                for row in data[table]:
                    vals = [row.get(c, '') for c in col_list]
                    placeholders = ','.join(['?'] * len(vals))
                    try:
                        db.execute(f'INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})', vals)
                    except Exception:
                        continue
                restored[table] = len(data[table])
        db.commit()
        snapshot_net_worth(app)
        return jsonify({'ok': True, 'restored': restored})

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=False, host='127.0.0.1', port=5050)
