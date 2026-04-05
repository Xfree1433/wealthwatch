"""
WealthWatch - Personal Finance Dashboard
Factory pattern with blueprints.
"""
from flask import Flask, render_template, request, session, jsonify
from config import config_map
from app.services.licensing import get_license_status, data_dir
from app.services.database import init_db
import os
import secrets


def create_app(config_name=None):
    app = Flask(__name__)

    # Load config
    config_name = config_name or os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config_map.get(config_name, config_map['default']))

    # Store data in user's AppData (persists across exe updates)
    app_data = data_dir()
    app.config['DATABASE'] = os.path.join(app_data, 'wealthwatch.db')
    app.config['SECRET_KEY'] = os.environ.get('WW_SECRET_KEY', secrets.token_hex(32))
    app.instance_path = app_data
    app.config['APP_DATA'] = app_data

    os.makedirs(app_data, exist_ok=True)

    # Initialize database
    from app.services.database import init_app as db_init_app
    db_init_app(app)
    with app.app_context():
        init_db(app)

    # ── PIN setup ────────────────────────────────────────────────────────────
    pin_file = os.path.join(app_data, '.pin')
    if os.path.exists(pin_file):
        with open(pin_file) as _pf:
            app.config['DEFAULT_PIN'] = _pf.read().strip()
        app.config['FIRST_RUN'] = False
    else:
        app.config['DEFAULT_PIN'] = secrets.token_hex(3)[:6]
        with open(pin_file, 'w') as _pf:
            _pf.write(app.config['DEFAULT_PIN'])
        app.config['FIRST_RUN'] = True

    # ── CSRF protection ──────────────────────────────────────────────────────
    @app.before_request
    def csrf_protect():
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        if request.path == '/login':
            return
        if request.path.startswith('/api/'):
            return  # API routes protected by session auth + same-origin policy
        token = request.form.get('_csrf') or request.headers.get('X-CSRF-Token')
        if token != session.get('csrf_token'):
            return jsonify({'error': 'CSRF token invalid'}), 403

    @app.context_processor
    def inject_csrf():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        return {'csrf_token': session['csrf_token']}

    # ── Global context ───────────────────────────────────────────────────────
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

    # ── Register blueprints ──────────────────────────────────────────────────
    from app.blueprints.auth import auth_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.accounts import accounts_bp
    from app.blueprints.transactions import transactions_bp
    from app.blueprints.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(reports_bp)

    # ── Error handlers ───────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Not found'}), 404
        return render_template('error.html', code=404, message='Page not found'), 404

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('error.html', code=500, message='Something went wrong'), 500

    return app
