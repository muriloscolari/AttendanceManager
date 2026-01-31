from app import create_app

app = create_app()

if __name__ == '__main__':
    import os
    is_debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=is_debug_mode, host='0.0.0.0', port=5000)
