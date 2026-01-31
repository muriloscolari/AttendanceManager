from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from app.config import Config
from app.extensions import db, login_manager
from app.utils import generate_google_maps_url

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize Extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = "Por favor, faça login para acessar esta página."
    login_manager.login_message_category = "info"

    # Context Processors
    @app.context_processor
    def inject_current_year():
        return {'current_year': datetime.now(Config.BRASILIA_TZ).year, 'generate_google_maps_url': generate_google_maps_url}

    # Register Blueprints
    from app.views import auth, main, party, payment, public
    app.register_blueprint(auth.bp, url_prefix='/auth')
    app.register_blueprint(main.bp) # Main routes like /dashboard on root
    app.register_blueprint(party.bp, url_prefix='/party')
    app.register_blueprint(payment.bp) # Payment routes often need root access or specific paths
    app.register_blueprint(public.bp) # Public routes like /p/

    # Ensure storage directories exist
    import os
    if not os.path.exists(app.config['STORAGE_BASE_PATH']):
        os.makedirs(app.config['STORAGE_BASE_PATH'])
    if not os.path.exists(app.config['PARTY_LOGOS_SAVE_PATH']):
        os.makedirs(app.config['PARTY_LOGOS_SAVE_PATH'])
    if not os.path.exists(app.config['PAYMENT_QRCODES_SAVE_PATH']):
        os.makedirs(app.config['PAYMENT_QRCODES_SAVE_PATH'])
    
    # Instance folder
    if not os.path.exists(os.path.join(app.instance_path)):
         try:
             os.makedirs(app.instance_path)
         except OSError:
             pass

    return app
