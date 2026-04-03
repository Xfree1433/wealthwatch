"""
WealthWatch - Standard entry point
"""
from app import create_app

PORT = 5100
HOST = '127.0.0.1'

app = create_app()

if __name__ == '__main__':
    try:
        from waitress import serve
        print(f'\n  WealthWatch by PF9')
        print(f'  Running at http://{HOST}:{PORT} (waitress)\n')
        serve(app, host=HOST, port=PORT)
    except ImportError:
        print(f'\n  WealthWatch by PF9')
        print(f'  Running at http://{HOST}:{PORT} (Flask dev server)\n')
        app.run(host=HOST, port=PORT, debug=False)
