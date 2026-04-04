"""
WealthWatch Desktop Launcher
──────────────────────────────
Starts the local Flask server and opens the browser.
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


def is_already_running():
    """Check if another instance is running by trying to bind the lock port."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((HOST, LOCK_PORT))
        s.listen(1)
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


def main():
    # ── Single-instance check ───────────────────────────────────────────
    already_running, lock_socket = is_already_running()
    if already_running:
        port = find_running_port(PORT)
        if port:
            webbrowser.open(f'http://{HOST}:{port}')
        sys.exit(0)

    from app import create_app

    port = find_open_port(PORT)
    app = create_app()

    # ── Pending shutdown management ────────────────────────────────────────
    _shutdown_timer = [None]  # mutable container for thread reference
    _shutdown_lock = threading.Lock()

    def cancel_pending_shutdown():
        with _shutdown_lock:
            if _shutdown_timer[0] is not None:
                _shutdown_timer[0] = None

    @app.before_request
    def on_request():
        cancel_pending_shutdown()

    @app.route('/api/shutdown', methods=['POST'])
    def shutdown():
        def delayed_shutdown():
            time.sleep(5)
            with _shutdown_lock:
                if _shutdown_timer[0] == threading.current_thread():
                    os._exit(0)
        with _shutdown_lock:
            t = threading.Thread(target=delayed_shutdown, daemon=True)
            _shutdown_timer[0] = t
            t.start()
        return '', 204

    # ── Inject shutdown JS into every page ───────────────────────────────
    @app.after_request
    def inject_lifecycle_js(response):
        if response.content_type and 'text/html' in response.content_type:
            # beforeunload fires on tab close AND internal navigation.
            # Server cancels shutdown if a new request arrives within 5s.
            # Tab close = no new request = server exits.
            # Internal nav = new page loads within 1s = shutdown cancelled.
            js = b'''<script>
(function(){
    window.addEventListener('beforeunload', function(){
        navigator.sendBeacon('/api/shutdown');
    });
})();
</script>'''
            response.data = response.data.replace(b'</body>', js + b'</body>')
        return response

    print(f'\n  WealthWatch by PF9')
    print(f'  Personal Finance Dashboard')
    print(f'  Running at http://{HOST}:{port}')
    print(f'  Close the browser tab to stop.\n')

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
