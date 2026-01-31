from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User
from app.extensions import db

bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    next_page = request.args.get('next')

    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user, remember=True)
            return redirect(next_page or url_for('main.dashboard'))
        flash('Email ou senha inválidos.', 'danger')
    return render_template('login.html')

@bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    next_page = request.args.get('next')

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('Este nome de usuário já existe.', 'danger')
            return redirect(url_for('auth.signup', next=next_page))

        if User.query.filter_by(email=email).first():
            flash('Este email já está em uso.', 'danger')
            return redirect(url_for('auth.signup', next=next_page))

        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)
        flash('Conta criada com sucesso!', 'success')
        return redirect(next_page or url_for('main.dashboard'))
    return render_template('signup.html')

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.landing'))
