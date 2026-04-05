"""
Auth Blueprint - Login, Logout, PIN, License, Profile
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app
from app.extensions import login_required, _safe, _err
from app.services.licensing import get_license_status, activate_license, deactivate_license
import os
import json as jsonlib

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        pin = request.form.get('pin', '').strip()
        if pin == current_app.config['DEFAULT_PIN']:
            session['authed'] = True
            session.permanent = True
            current_app.config['FIRST_RUN'] = False
            return redirect(url_for('dashboard.dashboard'))
        error = 'Invalid PIN. Try again.'
    first_run = current_app.config.get('FIRST_RUN', False)
    pin = current_app.config['DEFAULT_PIN'] if first_run else None
    return render_template('login.html', error=error, first_run=first_run, generated_pin=pin)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


# ── Profile ──────────────────────────────────────────────────────────────────

@auth_bp.route('/api/profile', methods=['GET', 'POST'])
@login_required
def api_profile():
    app_data = current_app.config['APP_DATA']
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


# ── License routes ───────────────────────────────────────────────────────────

@auth_bp.route('/api/license')
@login_required
def api_license_status():
    return jsonify(get_license_status())


@auth_bp.route('/api/license/activate', methods=['POST'])
@login_required
def api_license_activate():
    d = request.get_json()
    key = d.get('key', '') if d else ''
    success, msg = activate_license(key)
    if success:
        return jsonify({'ok': True, 'message': msg})
    return _err(msg)


@auth_bp.route('/api/license/deactivate', methods=['POST'])
@login_required
def api_license_deactivate():
    deactivate_license()
    return jsonify({'ok': True})


# ── Shortcut creator ────────────────────────────────────────────────────────

@auth_bp.route('/api/create-shortcut', methods=['POST'])
def api_create_shortcut():
    import sys
    d = request.get_json() or {}
    location = d.get('location', 'desktop')

    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        # Dev mode: create shortcut that runs launcher.py via python
        exe_path = sys.executable  # python.exe
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'launcher.py')
        script_path = os.path.normpath(script_path)

    try:
        import subprocess
        if location == 'desktop':
            folder = subprocess.check_output(
                ['powershell', '-Command', '[Environment]::GetFolderPath("Desktop")'],
                text=True
            ).strip()
        elif location == 'startup':
            folder = subprocess.check_output(
                ['powershell', '-Command', '[Environment]::GetFolderPath("Startup")'],
                text=True
            ).strip()
        else:
            return _err('Invalid location')

        shortcut_path = os.path.join(folder, 'WealthWatch.lnk')
        # Use PowerShell to create a .lnk shortcut
        if getattr(sys, 'frozen', False):
            ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("{shortcut_path}")
$sc.TargetPath = "{exe_path}"
$sc.WorkingDirectory = "{os.path.dirname(exe_path)}"
$sc.Description = "WealthWatch by PF9"
$sc.Save()
'''
        else:
            working_dir = os.path.dirname(script_path)
            ps_script = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("{shortcut_path}")
$sc.TargetPath = "{exe_path}"
$sc.Arguments = '"{script_path}"'
$sc.WorkingDirectory = "{working_dir}"
$sc.Description = "WealthWatch by PF9"
$sc.Save()
'''
        subprocess.run(['powershell', '-Command', ps_script], check=True, capture_output=True)
        return jsonify({'ok': True, 'path': shortcut_path})
    except Exception as e:
        return _err(f'Failed to create shortcut: {str(e)}')
