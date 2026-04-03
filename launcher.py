"""
WealthWatch Desktop Launcher
──────────────────────────────
Starts the local Flask server and opens the browser.
"""
import sys
import os
import webbrowser
import threading
import socket

# Ensure we can find our modules when running as frozen exe
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
    sys.path.insert(0, os.path.dirname(sys.executable))

from app import create_app

PORT = 5050
HOST = '127.0.0.1'


def find_open_port(start=5050, end=5100):
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
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://{HOST}:{port}')


def main():
    port = find_open_port(PORT)
    app = create_app()

    print(f'\n  WealthWatch by PF9')
    print(f'  Personal Finance Dashboard')
    print(f'  Running at http://{HOST}:{port}')
    print(f'  Press Ctrl+C to stop\n')

    # Open browser in background thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    try:
        app.run(host=HOST, port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print('\nWealthWatch stopped.')
        sys.exit(0)


if __name__ == '__main__':
    main()
