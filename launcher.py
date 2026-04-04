"""
WealthWatch Desktop Launcher
──────────────────────────────
Starts the local Flask server and opens the browser.
Auto-shuts down when the browser tab is closed.
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

from app import create_app

PORT = 5100
HOST = '127.0.0.1'

# Tracks the last time the browser pinged us
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()


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
    """Shut down if no heartbeat received for 30 seconds."""
    while True:
        time.sleep(10)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        if elapsed > 30:
            os._exit(0)


def main():
    port = find_open_port(PORT)
    app = create_app()

    # ── Heartbeat + shutdown routes ──────────────────────────────────────
    @app.route('/api/heartbeat', methods=['POST'])
    def heartbeat():
        global _last_heartbeat
        with _heartbeat_lock:
            _last_heartbeat = time.time()
        return '', 204

    @app.route('/api/shutdown', methods=['POST'])
    def shutdown():
        # Give a moment for the response to send, then exit
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
        print('\nWealthWatch stopped.')
        sys.exit(0)


if __name__ == '__main__':
    main()
