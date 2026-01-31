from datetime import datetime
from flask import url_for
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db, login_manager
from app.config import Config

party_collaborators = db.Table('party_collaborators',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('party_id', db.Integer, db.ForeignKey('party.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    tax_id = db.Column(db.String(20), unique=True, nullable=True)
    cellphone = db.Column(db.String(20), nullable=True)
    parties = db.relationship('Party', backref='owner', lazy=True, cascade="all, delete-orphan")
    collaborations = db.relationship('Party', secondary=party_collaborators, lazy='subquery',
                                     backref=db.backref('collaborators', lazy=True))
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Party(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    party_code = db.Column(db.String(6), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(Config.BRASILIA_TZ))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    guests = db.relationship('Guest', backref='party', lazy=True, cascade="all, delete-orphan")
    logo_filename = db.Column(db.String(255), nullable=True)
    shareable_link_id = db.Column(db.String(16), unique=True, nullable=False)
    public_description = db.Column(db.Text, nullable=True)
    show_guest_count = db.Column(db.Boolean, nullable=False, default=True)
    share_code = db.Column(db.String(8), unique=True, nullable=False)
    ticket_price = db.Column(db.Float, default=0.0, nullable=False)
    allow_public_purchase = db.Column(db.Boolean, default=False, nullable=False)
    location = db.Column(db.String(255), nullable=True)
    event_date = db.Column(db.Date, nullable=True)
    event_time = db.Column(db.Time, nullable=True)
    invite_font = db.Column(db.String(100), nullable=False, default='Montserrat-Regular')

    @property
    def formatted_date(self):
        if self.event_date:
            return self.event_date.strftime('%d/%m/%Y')
        return None

    @property
    def formatted_time(self):
        if self.event_time:
            return self.event_time.strftime('%H:%M')
        return None

class Guest(db.Model):
    __tablename__ = 'guest'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    qr_hash = db.Column(db.String(64), unique=True, nullable=False)
    entered = db.Column(db.Boolean, default=False, nullable=False)
    check_in_time = db.Column(db.DateTime, nullable=True)
    party_id = db.Column(db.Integer, db.ForeignKey('party.id'), nullable=False)
    added_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    adder = db.relationship('User', foreign_keys=[added_by_user_id], backref='added_guests')
    payment_status = db.Column(db.String(30), default='not_applicable', nullable=False)
    payment_charge_id = db.Column(db.String(120), unique=True, nullable=True)
    pix_qr_code_filename = db.Column(db.String(255), nullable=True)
    pix_emv_code = db.Column(db.Text, nullable=True)
    pix_created_at = db.Column(db.DateTime, nullable=True)
    purchase_link_id = db.Column(db.String(64), unique=True, nullable=True)
    purchased_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    purchaser = db.relationship('User', foreign_keys=[purchased_by_user_id], backref='purchased_tickets')
    purchase_price = db.Column(db.Float, nullable=True)

    @property
    def qr_image_url(self):
        # Adjusted for Blueprint 'party'
        return url_for('party.serve_qr_code', qr_hash=self.qr_hash)

    @property
    def pix_qr_code_url(self):
        if self.pix_qr_code_filename:
            # Adjusted for Blueprint 'main'
            return url_for('main.serve_persistent_file', filename=f"{Config.PAYMENT_QRCODES_FOLDER_NAME}/{self.pix_qr_code_filename}")
        return None

    def get_check_in_time_str(self):
        if self.check_in_time:
            return self.check_in_time.astimezone(Config.BRASILIA_TZ).strftime('%d/%m/%Y %H:%M:%S')
        return "N/A"

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
