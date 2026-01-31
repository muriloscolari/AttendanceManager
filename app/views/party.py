import os
import uuid
import secrets
import string
import csv
import io
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort, Response
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.models import Party, Guest, User
from app.extensions import db
from app.config import Config
from app.utils import (
    generate_unique_code, generate_qr_code_image, get_party_stats_data,
    PDF, get_all_guests_for_export, delete_pix_qr_code_file
)
from app.views import check_collaboration_permission
from app.services.storage import get_storage_service

bp = Blueprint('party', __name__)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

@bp.route('/create', methods=['POST'])
@login_required
def create_party():
    party_name = request.form.get('party_name')
    if not party_name or not party_name.strip():
        flash('O nome da festa não pode ser vazio.', 'danger')
        return redirect(url_for('main.dashboard'))

    new_party = Party(name=party_name, owner=current_user, party_code=generate_unique_code(Party, 'party_code', 6), share_code=generate_unique_code(Party, 'share_code', 8), shareable_link_id=secrets.token_urlsafe(12))
    db.session.add(new_party)
    db.session.commit()

    flash(f'Festa "{party_name}" criada!', 'success')
    return redirect(url_for('party.party_manager', party_id=new_party.id))

@bp.route('/add_collaboration', methods=['POST'])
@login_required
def add_collaboration():
    share_code = request.form.get('share_code')
    party = Party.query.filter_by(share_code=share_code).first()

    if not party:
        flash('Código de compartilhamento inválido.', 'danger')
    elif party.owner == current_user:
        flash('Você não pode se adicionar como colaborador da sua própria festa.', 'warning')
    elif current_user in party.collaborators:
        flash('Você já é um colaborador desta festa.', 'info')
    else:
        party.collaborators.append(current_user)
        db.session.commit()
        flash(f'Você agora é um colaborador da festa "{party.name}"!', 'success')

    return redirect(url_for('main.dashboard'))

@bp.route('/<int:party_id>')
@login_required
def party_manager(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)
    return render_template('party_manager.html', party=party)

@bp.route('/<int:party_id>/delete', methods=['POST'])
@login_required
def delete_party(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    if party.user_id != current_user.id:
        flash("Apenas o dono da festa pode deletá-la.", "danger")
        return redirect(url_for('main.dashboard'))

    # Delete logo from S3 if exists
    if party.logo_filename:
        try:
            storage = get_storage_service()
            key = f"{Config.PARTY_LOGOS_FOLDER_NAME}/{party.logo_filename}"
            storage.delete_file(key)
        except Exception as e:
            current_app.logger.warning(f"Failed to delete logo from S3: {e}")

    db.session.delete(party)
    db.session.commit()

    flash(f'A festa "{party.name}" foi removida.', 'success')
    return redirect(url_for('main.dashboard'))

@bp.route('/<int:party_id>/upload_logo', methods=['POST'])
@login_required
def upload_logo(party_id):
    from app.services.storage import compress_image
    
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)
    if 'party_logo' not in request.files:
        return jsonify({'success': False, 'message': 'Nenhum arquivo selecionado.'}), 400
    file = request.files['party_logo']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Nenhum arquivo selecionado.'}), 400
    if file and allowed_file(file.filename):
        storage = get_storage_service()
        
        # Delete old logo from S3 if exists
        if party.logo_filename:
            try:
                old_key = f"{Config.PARTY_LOGOS_FOLDER_NAME}/{party.logo_filename}"
                storage.delete_file(old_key)
            except Exception as e:
                current_app.logger.warning(f"Failed to delete old logo: {e}")
        
        # Compress image before upload
        filename = secure_filename(file.filename)
        compressed_stream, new_filename, content_type = compress_image(file.stream, filename)
        
        # Add unique ID to filename
        unique_id = uuid.uuid4().hex
        base_name = new_filename.rsplit('.', 1)[0] if '.' in new_filename else new_filename
        ext = new_filename.rsplit('.', 1)[1] if '.' in new_filename else 'jpg'
        final_filename = f"{party_id}_{unique_id}.{ext}"
        
        # Upload compressed image to S3
        full_key = storage.upload_file(
            compressed_stream,
            final_filename,
            folder=Config.PARTY_LOGOS_FOLDER_NAME,
            content_type=content_type
        )
        
        if full_key:
            party.logo_filename = final_filename
            db.session.commit()
            # Generate presigned URL for immediate display
            logo_url = storage.get_presigned_url(full_key, expiration=3600)
            return jsonify({'success': True, 'message': 'Logo da festa atualizado!', 'logo_url': logo_url})
        else:
            return jsonify({'success': False, 'message': 'Erro ao fazer upload do logo.'}), 500
    else:
        return jsonify({'success': False, 'message': 'Tipo de arquivo inválido.'}), 400

@bp.route('/<int:party_id>/update_details', methods=['POST'])
@login_required
def update_party_details(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)
    data = request.get_json()

    new_party_name = data.get('party_name', '').strip()
    if not new_party_name:
        return jsonify({'success': False, 'message': 'O nome da festa não pode ser vazio.'}), 400
    party.name = new_party_name

    party.public_description = data.get('public_description')
    party.location = data.get('location')

    try:
        ticket_price_str = str(data.get('ticket_price', '0.0')).replace(',', '.')
        party.ticket_price = float(ticket_price_str)
        if party.ticket_price < 0:
            raise ValueError("Preço do ingresso não pode ser negativo.")
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Preço do ingresso inválido. Use um número (ex: 50.00).'}), 400

    party.allow_public_purchase = bool(data.get('allow_public_purchase'))

    event_date_str = data.get('event_date')
    event_time_str = data.get('event_time')

    if event_date_str:
        try:
            party.event_date = datetime.strptime(event_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': 'Formato de data inválido. Use AAAA-MM-DD.'}), 400
    else:
        party.event_date = None

    if event_time_str:
        try:
            party.event_time = datetime.strptime(event_time_str, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'message': 'Formato de hora inválido. Use HH:MM.'}), 400
    else:
        party.event_time = None

    db.session.commit()
    return jsonify({'success': True, 'message': 'Informações da festa atualizadas!', 'party_name': party.name})

@bp.route('/<int:party_id>/toggle_guest_count', methods=['POST'])
@login_required
def toggle_guest_count(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)
    party.show_guest_count = not party.show_guest_count
    db.session.commit()
    return jsonify({
        'success': True,
        'message': 'Visibilidade da contagem de convidados atualizada.',
        'show_guest_count': party.show_guest_count
    })

# /qr/<hash>.png was separate in app.py. I'll include it here but url_prefix of blueprint is /party probably?
# No, if I set url_prefix='/party', then it becomes /party/qr/... which breaks existing links logic if they expected /qr/
# But app.py had `@app.route('/qr/<string:qr_hash>.png')`.
# I should register this route on the blueprint but I might need to alias it or handle prefix.
# If I make the blueprint prefix blank or '/', then I can define /qr/... here.
# But other routes are /party/...
# I can define routes as `/party/...` AND `/qr/...` in the same blueprint, IF the blueprint has NO prefix (or '/').
# I will register the 'party' blueprint with `url_prefix='/party'` for clean structure, but for `/qr/` I might need a separate route or put it in `main` or `public`.
# `serve_qr_code` seems public.
# However, logic requires checking guest info.
# AND `guest.qr_image_url` property in models.py uses `url_for('party.serve_qr_code')`.
# If I change the URL structure, existing QR codes (if they encoded the URL directly... wait, QR code encodes the HASH usually?
# In `app.py`: `qr_instance.add_data(qr_data)`. `qr_data` is passed as `guest.qr_hash`.
# The QR code contains the HASH.
# The scanner scans the HASH.
# The URL `/qr/<hash>.png` is for DISPLAYING the QR code image on the screen (the png file), NOT what's INSIDE the QR code.
# The `serve_qr_code` route returns the IMAGE of the QR code.
# So I can change this URL path safely as long as `url_for` is updated, which I did in `models.py`.
# So I will keep `serve_qr_code` inside `party` blueprint. 
# If 'party' blueprint has prefix `/party`, the URL will be `/party/qr/<hash>.png`.
# This is fine.

@bp.route('/qr/<string:qr_hash>.png')
def serve_qr_code(qr_hash):
    guest = Guest.query.filter_by(qr_hash=qr_hash).first_or_404()
    if guest.payment_status == 'paid' or guest.payment_status == 'not_applicable':
        img_buffer = generate_qr_code_image(guest.qr_hash, guest.name, guest.party)
        if img_buffer:
            return Response(img_buffer, mimetype='image/png')
        else:
            abort(500, description="Falha ao gerar a imagem do QR Code.")
    else:
        flash("O ingresso ainda não foi pago.", "warning")
        if guest.payment_charge_id:
            return redirect(url_for('payment.show_ticket_payment', payment_charge_id=guest.payment_charge_id))
        elif guest.purchase_link_id:
            return redirect(url_for('payment.buy_ticket_owner_invite', purchase_link_id=guest.purchase_link_id))
        else:
            abort(403, description="Ingresso não disponível.")

@bp.route('/api/<int:party_id>/preview_invite', methods=['GET'])
@login_required
def preview_invite(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)

    test_guest_name = "Nome do Convidado Teste"
    test_qr_hash = "PREVIEW_QR_HASH"
    font_to_use = request.args.get('font_name', party.invite_font)

    img_buffer = generate_qr_code_image(test_qr_hash, test_guest_name, party, font_override=font_to_use)
    if img_buffer:
        return Response(img_buffer, mimetype='image/png')
    else:
        abort(500, description="Falha ao gerar a imagem de pré-visualização do convite.")

@bp.route('/api/<int:party_id>/stats', methods=['GET'])
def get_stats(party_id):
    return jsonify(get_party_stats_data(party_id))

@bp.route('/api/<int:party_id>/font_selection', methods=['GET', 'POST'])
@login_required
def handle_font_selection(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)

    if request.method == 'GET':
        return jsonify({'selected_font': party.invite_font})

    elif request.method == 'POST':
        data = request.get_json()
        font_name = data.get('font_name')
        if not font_name:
            return jsonify({'message': 'Nome da fonte não fornecido.'}), 400
        
        allowed_fonts = ['Birthstone-Regular', 'Ephesis-Regular', 'Montserrat-Regular', 'Radley-Regular', 'BebasNeue-Regular', 'Cinzel-Regular', 'JosefinSans-Regular']
        if font_name not in allowed_fonts:
            return jsonify({'message': 'Fonte selecionada inválida.'}), 400

        party.invite_font = font_name
        db.session.commit()
        return jsonify({'message': 'Fonte do convite atualizada com sucesso!', 'selected_font': party.invite_font})

@bp.route('/api/<int:party_id>/guests', methods=['GET', 'POST'])
@login_required
def handle_guests(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)

    if request.method == 'POST':
        data = request.get_json()
        name = data.get('name', '').strip()
        if not name: return jsonify({'error': 'O nome do convidado é obrigatório.'}), 400

        new_guest = Guest(
            name=name,
            qr_hash=generate_unique_code(Guest, 'qr_hash', 32, string.ascii_letters + string.digits),
            party_id=party_id,
            added_by_user_id=current_user.id,
            payment_status='not_applicable',
            purchase_price=0.0
        )
        db.session.add(new_guest)
        db.session.commit()
        return jsonify({
            'id': new_guest.id, 'name': new_guest.name, 'qr_hash': new_guest.qr_hash, 'entered': new_guest.entered,
            'qr_image_url': new_guest.qr_image_url, 'check_in_time': new_guest.get_check_in_time_str(),
            'added_by': new_guest.adder.username, 'payment_status': new_guest.payment_status
        }), 201

    page, per_page, search_term = request.args.get('page', 1, type=int), request.args.get('per_page', 50, type=int), request.args.get('search')
    sort_by, sort_dir = request.args.get('sort_by', 'name'), request.args.get('sort_dir', 'asc')
    query = Guest.query.filter_by(party_id=party_id)
    if search_term: query = query.filter(Guest.name.ilike(f'%{search_term.strip()}%'))

    sort_columns = {'name': Guest.name, 'entered': Guest.entered, 'check_in_time': Guest.check_in_time, 'added_by': User.username, 'payment_status': Guest.payment_status}

    if sort_by == 'added_by':
        query = query.join(User, Guest.added_by_user_id == User.id)

    order_column = sort_columns.get(sort_by, Guest.name)
    order_func = order_column.desc() if sort_dir == 'desc' else order_column.asc()

    pagination = query.order_by(order_func.nullslast()).paginate(page=page, per_page=per_page, error_out=False)

    guests_data = []
    for g in pagination.items:
        purchased_by_name = g.purchaser.username if g.purchaser else None

        guests_data.append({
            'id': g.id, 'name': g.name, 'qr_hash': g.qr_hash, 'entered': g.entered, 'qr_image_url': g.qr_image_url,
            'check_in_time': g.get_check_in_time_str(), 'added_by': g.adder.username if g.adder else 'N/A',
            'payment_status': g.payment_status, 'purchased_by': purchased_by_name, 'purchase_link_id': g.purchase_link_id,
            'purchase_price': g.purchase_price
        })

    return jsonify({
        'guests': guests_data,
        'pagination': {
            'page': pagination.page, 'per_page': pagination.per_page, 'total_pages': pagination.pages,
            'total_items': pagination.total, 'has_next': pagination.has_next, 'has_prev': pagination.has_prev
        }
    })

@bp.route('/api/<int:party_id>/guests/<qr_hash>/enter', methods=['POST'])
def mark_entered(party_id, qr_hash):
    guest = Guest.query.filter_by(qr_hash=qr_hash, party_id=party_id).first()
    if not guest: return jsonify({'error': 'QR Code inválido para esta festa'}), 404

    if guest.party.ticket_price > 0 and guest.payment_status != 'paid':
        return jsonify({
            'id': guest.id, 'name': guest.name, 'qr_hash': guest.qr_hash, 'entered': False,
            'message': f'Ingresso de {guest.name} não foi pago (Status: {guest.payment_status}).',
            'is_new_entry': False, 'check_in_time': guest.get_check_in_time_str(), 'error_type': 'payment_pending'
        }), 403

    if guest.entered:
        return jsonify({'id': guest.id, 'name': guest.name, 'qr_hash': guest.qr_hash, 'entered': True, 'message': f'{guest.name} já entrou às {guest.get_check_in_time_str()}.', 'is_new_entry': False, 'check_in_time': guest.get_check_in_time_str()})

    guest.entered, guest.check_in_time = True, datetime.now(Config.BRASILIA_TZ)
    db.session.commit()
    return jsonify({'id': guest.id, 'name': guest.name, 'qr_hash': guest.qr_hash, 'entered': True, 'message': f'Entrada liberada! Bem-vindo(a), {guest.name}!', 'is_new_entry': True, 'check_in_time': guest.get_check_in_time_str()})

@bp.route('/api/<int:party_id>/checkin_data', methods=['GET'])
@login_required
def get_checkin_data(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)

    check_ins = db.session.query(Guest.check_in_time).filter(
        Guest.party_id == party_id,
        Guest.entered == True,
        Guest.check_in_time.isnot(None)
    ).all()

    timestamps = [
        check_in_time[0].astimezone(Config.BRASILIA_TZ).isoformat()
        for check_in_time in check_ins
    ]

    return jsonify({'check_ins': timestamps})

@bp.route('/api/<int:party_id>/guests/<qr_hash>/edit', methods=['PUT'])
@login_required
def edit_guest_name(party_id, qr_hash):
    guest = Guest.query.filter_by(qr_hash=qr_hash, party_id=party_id).first_or_404()
    check_collaboration_permission(guest.party)
    data = request.get_json()
    new_name = data.get('name', '').strip()

    guest.name = new_name
    db.session.commit()
    return jsonify({'id': guest.id, 'name': guest.name, 'qr_hash': guest.qr_hash, 'entered': guest.entered, 'qr_image_url': guest.qr_image_url, 'check_in_time': guest.get_check_in_time_str(), 'message': f'Nome do convidado atualizado.'}), 200

@bp.route('/api/<int:party_id>/guests/<qr_hash>/toggle_entry', methods=['PUT'])
@login_required
def toggle_entry_manually(party_id, qr_hash):
    guest = Guest.query.filter_by(qr_hash=qr_hash, party_id=party_id).first_or_404()
    check_collaboration_permission(guest.party)

    if not guest.entered and guest.payment_status != 'paid' and guest.party.ticket_price > 0:
        pass

    guest.entered = not guest.entered
    guest.check_in_time = datetime.now(Config.BRASILIA_TZ) if guest.entered else None
    action = "marcado(a) como PRESENTE" if guest.entered else "marcado(a) como AUSENTE"
    db.session.commit()
    return jsonify({'id': guest.id, 'name': guest.name, 'qr_hash': guest.qr_hash, 'entered': guest.entered, 'message': f'Status de {guest.name} alterado: {action}.', 'check_in_time': guest.get_check_in_time_str()})

@bp.route('/api/<int:party_id>/guests/<qr_hash>', methods=['DELETE'])
@login_required
def delete_guest(party_id, qr_hash):
    guest = Guest.query.filter_by(qr_hash=qr_hash, party_id=party_id).first_or_404()
    check_collaboration_permission(guest.party)
    guest_name = guest.name

    if guest.payment_status == 'paid':
        flash(f"Atenção: Você está deletando um ingresso que JÁ FOI PAGO por {guest_name}.", "warning")

    delete_pix_qr_code_file(guest)

    db.session.delete(guest)
    db.session.commit()
    return jsonify({'message': f'Convidado {guest_name} removido com sucesso.'}), 200

@bp.route('/api/<int:party_id>/export/csv')
@login_required
def export_guests_csv(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)
    guests_data = get_all_guests_for_export(party_id)
    si = io.StringIO()
    cw = csv.writer(si)

    cw.writerow(['Nome', 'Status Entrada', 'Data Check-in', 'Status Pagamento', 'Preço Pago', 'Comprado Por', 'Adicionado Por'])

    for guest in guests_data:
        payment_status_display = {
            'not_applicable': 'Gratuito',
            'pending_owner_invite': 'Aguardando Pagamento (Dono)',
            'pending': 'Aguardando Pagamento',
            'paid': 'Pago',
            'failed': 'Pagamento Falhou'
        }.get(guest.payment_status, guest.payment_status)

        purchase_price_export = f"{guest.purchase_price:.2f}".replace('.', ',') if guest.purchase_price is not None else 'N/A'
        purchased_by_name = guest.purchaser.username if guest.purchaser else 'N/A'
        added_by_name = guest.adder.username if guest.adder else 'N/A'

        cw.writerow([
            guest.name,
            'Sim' if guest.entered else 'Não',
            guest.get_check_in_time_str(),
            payment_status_display,
            purchase_price_export,
            purchased_by_name,
            added_by_name
        ])
    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-disposition": f"attachment; filename=lista_{party.name.replace(' ', '_')}.csv"})

@bp.route('/api/<int:party_id>/export/pdf')
@login_required
def export_guests_pdf(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)

    guests_for_table, stats_for_chart = get_all_guests_for_export(party_id), get_party_stats_data(party_id)

    pdf = PDF(party_name=party.name)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.draw_stats_summary(stats_for_chart)
    pdf.ln(10) 

    pdf.set_font(pdf.current_font_family, 'B', 11)
    pdf.cell(0, 10, "Gráfico de Comparecimento", border=0, new_x='LMARGIN', new_y='NEXT', align='L')
    pdf.ln(1)
    pdf.draw_pie_chart(stats_for_chart, pdf.get_y())
    pdf.ln(0)

    if guests_for_table:
        pdf.chapter_body(guests_for_table)
    else:
        pdf.set_font(pdf.current_font_family, 'I', 10)
        pdf.cell(0, 10, "Nenhum convidado na lista.", 0, 1, 'C')

    timestamp = datetime.now(Config.BRASILIA_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"relatorio_{party.name.replace(' ', '_')}_{timestamp}.pdf"

    pdf_output = bytes(pdf.output(dest='S'))

    return Response(pdf_output, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename={filename}'
    })
