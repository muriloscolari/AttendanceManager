import os
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_from_directory, abort, current_app
from flask_login import login_required, current_user
from app.models import User, Guest, Party
from app.extensions import db
from app.config import Config
from app.services.storage import get_storage_service

bp = Blueprint('main', __name__)

@bp.route('/')
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('landing.html')

@bp.route('/about')
def about():
    return render_template('about.html')

@bp.route('/dashboard')
@login_required
def dashboard():
    owned_parties = current_user.parties
    collaborated_parties = current_user.collaborations
    return render_template('dashboard.html', owned_parties=owned_parties, collaborated_parties=collaborated_parties)

@bp.route('/my_invitations')
@login_required
def my_invitations():
    """
    Displays all tickets/invitations purchased by the currently logged-in user.
    """
    # Assuming user.id relates to Guest.purchased_by_user_id
    invitations = Guest.query.filter_by(purchased_by_user_id=current_user.id).order_by(Guest.id.desc()).all()
    return render_template('my_invitations.html', invitations=invitations)

@bp.route('/complete_profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if request.method == 'POST':
        tax_id = request.form.get('tax_id', '').strip()
        cellphone = request.form.get('cellphone', '').strip()
        if not tax_id or not cellphone:
            flash("CPF e Telefone são obrigatórios.", 'danger')
            return render_template('complete_profile.html')

        if not tax_id.isdigit() or len(tax_id) not in [11, 14]:
            flash("CPF/CNPJ inválido. Digite apenas números.", 'danger')
            return render_template('complete_profile.html')
        if not cellphone.isdigit() or len(cellphone) not in [10, 11, 12, 13]:
             flash("Telefone inválido. Digite apenas números.", 'danger')
             return render_template('complete_profile.html')

        if User.query.filter(User.tax_id == tax_id, User.id != current_user.id).first():
            flash("Este CPF/CNPJ já está associado a outra conta.", 'danger')
            return render_template('complete_profile.html')

        current_user.tax_id, current_user.cellphone = tax_id, cellphone
        db.session.commit()
        flash("Perfil atualizado! Agora você já pode comprar ingressos.", 'success')

        next_page = request.args.get('next')
        return redirect(next_page or url_for('main.dashboard'))

    return render_template('complete_profile.html')

@bp.route('/persistent/<path:filename>')
def serve_persistent_file(filename):
    """
    Serve arquivo do S3 com cache em memória.
    Evita problemas de CORS e melhora performance.
    """
    from flask import Response
    
    storage = get_storage_service()
    data, content_type = storage.get_file_cached(filename)
    
    if data is None:
        current_app.logger.error(f"Arquivo não encontrado: {filename}")
        abort(404)
    
    return Response(
        data,
        mimetype=content_type,
        headers={
            'Cache-Control': 'public, max-age=31536000',
            'Content-Disposition': 'inline'
        }
    )
