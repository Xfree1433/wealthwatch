"""
WealthWatch Desktop Launcher
──────────────────────────────
Starts the local Flask server and opens the browser.
Auto-shuts down when the browser tab is closed.
Single-instance: if already running, opens a new browser tab instead.
"""
import sys
import os
import webbrowser
import threading
import socket
import time

# Ensure we can find our modules when running as frozen exe
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
    sys.path.insert(0, os.path.dirname(sys.executable))

PORT = 5100
HOST = '127.0.0.1'
LOCK_PORT = 5099  # Dedicated port for single-instance lock

# Tracks the last time the browser pinged us
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()


def is_already_running():
    """Check if another instance is running by trying to bind the lock port."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((HOST, LOCK_PORT))
        s.listen(1)
        # Keep socket open for lifetime of process (prevents other instances)
        return False, s
    except OSError:
        return True, None


def find_running_port(start=5100, end=5150):
    """Find which port the existing instance is running on."""
    for port in range(start, end):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect((HOST, port))
            s.close()
            return port
        except (OSError, ConnectionRefusedError):
            continue
    return None


def find_open_port(start=5100, end=5150):
    """Find an available port."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    return start


def open_browser(port):
    """Open the default browser after a short delay."""
    time.sleep(1.5)
    webbrowser.open(f'http://{HOST}:{port}')


def watchdog():
    """Shut down if no activity for 5 minutes (browser tab closed)."""
    time.sleep(120)  # 2 min grace period for startup + first-run reading
    while True:
        time.sleep(15)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        if elapsed > 300:  # 5 minutes of no requests = browser is gone
            os._exit(0)


def main():
    # ── Single-instance check ───────────────────────────────────────────
    already_running, lock_socket = is_already_running()
    if already_running:
        # Another instance exists — just open the browser to it
        port = find_running_port(PORT)
        if port:
            webbrowser.open(f'http://{HOST}:{port}')
        sys.exit(0)

    from app import create_app

    port = find_open_port(PORT)
    app = create_app()

    # ── Keep-alive: reset heartbeat on every request ───────────────────
    @app.before_request
    def reset_heartbeat():
        global _last_heartbeat
        with _heartbeat_lock:
            _last_heartbeat = time.time()

    # ── Heartbeat + shutdown routes ──────────────────────────────────────
    @app.route('/api/heartbeat', methods=['POST'])
    def heartbeat():
        return '', 204

    @app.route('/api/shutdown', methods=['POST'])
    def shutdown():
        threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
        return '', 204

    # ── Inject heartbeat + shutdown JS into every page ───────────────────
    @app.after_request
    def inject_lifecycle_js(response):
        if response.content_type and 'text/html' in response.content_type:
            js = b'''<script>
(function(){
    setInterval(function(){fetch('/api/heartbeat',{method:'POST'}).catch(function(){})},10000);
    window.addEventListener('beforeunload',function(){navigator.sendBeacon('/api/shutdown')});
})();
</script>'''
            response.data = response.data.replace(b'</body>', js + b'</body>')
        return response

    print(f'\n  WealthWatch by PF9')
    print(f'  Personal Finance Dashboard')
    print(f'  Running at http://{HOST}:{port}')
    print(f'  Close the browser tab to stop.\n')

    # Start watchdog and browser threads
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    try:
        app.run(host=HOST, port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        if lock_socket:
            lock_socket.close()
        print('\nWealthWatch stopped.')
        sys.exit(0)


if __name__ == '__main__':
    main()
