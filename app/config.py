import os
import pytz

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    ABACATE_API_KEY = os.environ.get('ABACATE_API_KEY')
    
    # --- Configuração do Caminho de Armazenamento Persistente ---
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    STORAGE_BASE_PATH = os.environ.get("STORAGE_BASE_PATH") or os.path.join(os.getcwd(), "persistent_storage")
    if not STORAGE_BASE_PATH:
        STORAGE_BASE_PATH = os.path.join(basedir, 'data')

    PARTY_LOGOS_FOLDER_NAME = 'party_logos'
    PAYMENT_QRCODES_FOLDER_NAME = 'payment_qrcodes'
    PARTY_LOGOS_SAVE_PATH = os.path.join(STORAGE_BASE_PATH, PARTY_LOGOS_FOLDER_NAME)
    PAYMENT_QRCODES_SAVE_PATH = os.path.join(STORAGE_BASE_PATH, PAYMENT_QRCODES_FOLDER_NAME)
    
    FONT_PATH = os.path.join(basedir, "app", "static", "fonts", "Montserrat-Regular.ttf")
    BRASILIA_TZ = pytz.timezone('America/Sao_Paulo')
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

    # Database
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
