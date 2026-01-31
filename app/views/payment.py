import secrets
import string
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app, abort
from flask_login import login_required, current_user

from app.models import Party, Guest, User
from app.extensions import db
from app.config import Config
from app.utils import (
    generate_unique_code, create_abacatepay_charge, save_base64_as_png,
    check_abacatepay_status, delete_pix_qr_code_file
)
from app.views import check_collaboration_permission

bp = Blueprint('payment', __name__)

@bp.route('/party/<int:party_id>/register_guest_for_payment', methods=['POST'])
@login_required
def register_guest_for_payment(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    check_collaboration_permission(party)
    data = request.get_json()
    guest_name = data.get('guest_name_for_payment', '').strip()

    if party.ticket_price <= 0:
        return jsonify({'status': 'error','message': 'Defina um preço para o ingresso na seção "Opções da Festa".'}), 400
    if not guest_name:
        return jsonify({'status': 'error','message': 'Nome do convidado para pagamento é obrigatório.'}), 400

    if not current_user.tax_id or not current_user.cellphone:
        next_url = url_for('party.party_manager', party_id=party_id)
        complete_profile_url = url_for('main.complete_profile', next=next_url)
        return jsonify({
            'status': 'error',
            'message': 'Seu perfil precisa ter CPF/CNPJ e Telefone para gerar links de pagamento. Redirecionando...',
            'action': 'redirect',
            'url': complete_profile_url
        }), 400

    qr_hash = generate_unique_code(Guest, 'qr_hash', 32, string.ascii_letters + string.digits)
    purchase_link_id = generate_unique_code(Guest, 'purchase_link_id', 24)

    try:
        customer_info = { "name": guest_name, "email": current_user.email, "taxId": current_user.tax_id, "cellphone": current_user.cellphone }
        charge_data = create_abacatepay_charge(amount=party.ticket_price, description=f"Ingresso {party.name} - {guest_name}", customer_info=customer_info)
        qr_filename = save_base64_as_png(charge_data.get('brCodeBase64'), charge_data['id'])
        if not qr_filename:
            raise ValueError("Falha ao criar o arquivo PNG do QR Code.")

        new_guest = Guest(
            name=guest_name,
            qr_hash=qr_hash,
            party_id=party_id,
            added_by_user_id=current_user.id,
            payment_status='pending_owner_invite',
            payment_charge_id=charge_data['id'],
            pix_qr_code_filename=qr_filename,
            pix_emv_code=charge_data.get('brCode'),
            pix_created_at=datetime.now(Config.BRASILIA_TZ),
            purchase_link_id=purchase_link_id,
            purchase_price=party.ticket_price
        )
        db.session.add(new_guest)
        db.session.commit()

        payment_link = url_for('payment.buy_ticket_owner_invite', purchase_link_id=purchase_link_id, _external=True)
        return jsonify({
            'status': 'success',
            'message': f'Link de pagamento gerado para {guest_name}.',
            'payment_link': payment_link
        })
    except Exception as e:
        current_app.logger.error(f"Erro ao gerar link de pagamento: {e}")
        return jsonify({'status': 'error', 'message': 'Erro ao se comunicar com o serviço de pagamento.'}), 500

@bp.route('/buy_ticket/owner_invite/<string:purchase_link_id>')
def buy_ticket_owner_invite(purchase_link_id):
    guest = Guest.query.filter_by(purchase_link_id=purchase_link_id).first_or_404()
    party = guest.party
    if guest.payment_status == 'paid':
        return redirect(url_for('payment.show_ticket_payment', payment_charge_id=guest.payment_charge_id))
    if guest.payment_charge_id and guest.payment_status in ['pending', 'pending_owner_invite']:
        return redirect(url_for('payment.show_ticket_payment', payment_charge_id=guest.payment_charge_id))

    if not guest.payment_charge_id or not guest.pix_qr_code_filename or guest.payment_status == 'failed':
        try:
            customer_info = { "name": guest.name, "email": guest.adder.email if guest.adder and guest.adder.email else "sememail@qrpass.com.br", "taxId": guest.adder.tax_id if guest.adder and guest.adder.tax_id else "00000000000", "cellphone": guest.adder.cellphone if guest.adder and guest.adder.cellphone else "00000000000" }
            charge_data = create_abacatepay_charge(amount=party.ticket_price, description=f"Ingresso {party.name} - {guest.name}", customer_info=customer_info)
            qr_filename = save_base64_as_png(charge_data.get('brCodeBase64'), charge_data['id'])
            if not qr_filename:
                raise ValueError("Falha ao criar o arquivo PNG do QR Code.")

            guest.payment_charge_id = charge_data['id']
            guest.payment_status = 'pending_owner_invite'
            guest.pix_qr_code_filename = qr_filename
            guest.pix_emv_code = charge_data.get('brCode')
            guest.pix_created_at = datetime.now(Config.BRASILIA_TZ)
            guest.purchase_price = party.ticket_price
            db.session.commit()
        except Exception as e:
            current_app.logger.error(f"Erro na API ao gerar PIX para convite: {e}")
            flash('Não foi possível gerar a cobrança PIX. Tente novamente.', 'danger')
            return render_template('show_ticket_payment.html', guest=guest, payment_pending=False, error_message="Falha ao gerar Pix.")

    return redirect(url_for('payment.show_ticket_payment', payment_charge_id=guest.payment_charge_id))

@bp.route('/buy_ticket/party/<int:party_id>', methods=['GET', 'POST'])
def buy_ticket_public(party_id):
    party = db.session.get(Party, party_id) or abort(404)
    if not party.allow_public_purchase:
        flash('Compra de ingressos online não está disponível para esta festa.', 'info')
        # Referencing public.public_party_page
        return redirect(url_for('public.public_party_page', shareable_link_id=party.shareable_link_id))

    if not current_user.is_authenticated:
        flash('Faça login ou cadastre-se para comprar seu ingresso.', 'info')
        return redirect(url_for('auth.login', next=request.url))

    if not current_user.tax_id or not current_user.cellphone:
        flash("Por favor, complete seu perfil com CPF/CNPJ e Telefone para comprar ingressos.", 'warning')
        return redirect(url_for('main.complete_profile', next=request.url))

    if party.ticket_price == 0:
        existing_guest = Guest.query.filter_by(party_id=party_id, purchased_by_user_id=current_user.id, payment_status='paid').first()
        if existing_guest:
            flash(f'Você já possui um ingresso para {party.name}! Você pode visualizá-lo abaixo.', 'info')
            return render_template('show_ticket_payment.html', guest=existing_guest, payment_pending=False)

        guest_name = current_user.username
        qr_hash = generate_unique_code(Guest, 'qr_hash', 32, string.ascii_letters + string.digits)

        new_guest = Guest(
            name=guest_name,
            qr_hash=qr_hash,
            party_id=party_id,
            added_by_user_id=current_user.id,
            payment_status='paid',
            purchased_by_user_id=current_user.id,
            purchase_price=0.0
        )
        db.session.add(new_guest)
        db.session.commit()
        flash(f'Seu ingresso para {party.name} está pronto! Bem-vindo(a), {guest_name}!', 'success')
        return render_template('show_ticket_payment.html', guest=new_guest, payment_pending=False)

    if request.method == 'POST':
        guest_name = request.form.get('guest_name', current_user.username).strip()
        if not guest_name:
            flash('Nome do convidado é obrigatório.', 'danger')
            return render_template('buy_ticket_public.html', party=party, ticket_price_display=f"{party.ticket_price:.2f}".replace('.', ','))

        existing_paid_guest = Guest.query.filter_by(party_id=party_id, purchased_by_user_id=current_user.id, payment_status='paid').first()
        if existing_paid_guest:
            flash(f'Você já possui um ingresso pago para {party.name} em seu nome!', 'info')
            return redirect(url_for('payment.show_ticket_payment', payment_charge_id=existing_paid_guest.payment_charge_id))

        try:
            customer_info = { "name": guest_name, "email": current_user.email, "taxId": current_user.tax_id, "cellphone": current_user.cellphone }
            description = f"Ingresso {party.name} - {guest_name}"
            charge_data = create_abacatepay_charge(amount=party.ticket_price, description=description, customer_info=customer_info)
            qr_filename = save_base64_as_png(charge_data.get('brCodeBase64'), charge_data['id'])
            if not qr_filename:
                raise ValueError("Falha ao criar o arquivo PNG do QR Code.")

            existing_pending_guest = Guest.query.filter_by(party_id=party_id, purchased_by_user_id=current_user.id, payment_status='pending').first()

            if existing_pending_guest:
                guest_to_update = existing_pending_guest
                guest_to_update.name = guest_name
            else:
                qr_hash = generate_unique_code(Guest, 'qr_hash', 32, string.ascii_letters + string.digits)
                guest_to_update = Guest(name=guest_name, qr_hash=qr_hash, party_id=party_id, added_by_user_id=current_user.id, purchased_by_user_id=current_user.id)
                db.session.add(guest_to_update)

            guest_to_update.payment_charge_id = charge_data['id']
            guest_to_update.payment_status = 'pending'
            guest_to_update.pix_qr_code_filename = qr_filename
            guest_to_update.pix_emv_code = charge_data.get('brCode')
            guest_to_update.pix_created_at = datetime.now(Config.BRASILIA_TZ)
            guest_to_update.purchase_price = party.ticket_price
            db.session.commit()
            return redirect(url_for('payment.show_ticket_payment', payment_charge_id=charge_data['id']))
        except Exception as e:
            current_app.logger.error(f"Erro na API ao comprar ingresso: {e}")
            flash('Erro ao gerar a cobrança Pix. Tente novamente mais tarde.', 'danger')
            return render_template('buy_ticket_public.html', party=party, ticket_price_display=f"{party.ticket_price:.2f}".replace('.', ','), guest_name=guest_name)

    return render_template('buy_ticket_public.html', party=party, ticket_price_display=f"{party.ticket_price:.2f}".replace('.', ','))

@bp.route('/show_ticket_payment/<string:payment_charge_id>')
def show_ticket_payment(payment_charge_id):
    guest_ticket_payment = Guest.query.filter_by(payment_charge_id=payment_charge_id).first_or_404()

    pix_creation_iso = guest_ticket_payment.pix_created_at.isoformat() if guest_ticket_payment.pix_created_at else None

    if guest_ticket_payment.payment_status == 'paid':
        flash(f"Seu ingresso para {guest_ticket_payment.party.name} já foi pago!", "success")
        return render_template('show_ticket_payment.html',
                               guest=guest_ticket_payment,
                               payment_pending=False,
                               pix_creation_iso=pix_creation_iso)

    elif guest_ticket_payment.payment_status in ['pending', 'pending_owner_invite']:
        return render_template('show_ticket_payment.html',
                               guest=guest_ticket_payment,
                               payment_pending=True,
                               pix_creation_iso=pix_creation_iso)
    else:
        flash("Sua cobrança expirou ou falhou. Por favor, gere uma nova.", "warning")
        if guest_ticket_payment.purchase_link_id:
            return redirect(url_for('payment.buy_ticket_owner_invite', purchase_link_id=guest_ticket_payment.purchase_link_id))
        elif guest_ticket_payment.purchased_by_user_id:
            return redirect(url_for('payment.buy_ticket_public', party_id=guest_ticket_payment.party_id))
        else:
            return redirect(url_for('public.public_party_page', shareable_link_id=guest_ticket_payment.party.shareable_link_id))

@bp.route('/payment/check_status/<pix_id>')
def check_payment_status(pix_id):
    is_guest_ticket_payment = Guest.query.filter_by(payment_charge_id=pix_id).first()

    if not is_guest_ticket_payment:
        return jsonify({'status': 'error', 'message': 'Cobrança não encontrada.'}), 404

    try:
        data = check_abacatepay_status(pix_id)
        status = data.get('status')

        if status == 'PAID':
            if is_guest_ticket_payment.payment_status != 'paid':
                is_guest_ticket_payment.payment_status = 'paid'
                if is_guest_ticket_payment.purchase_price is None:
                    is_guest_ticket_payment.purchase_price = is_guest_ticket_payment.party.ticket_price
                db.session.commit()
                delete_pix_qr_code_file(is_guest_ticket_payment)
                return jsonify({'status': 'PAID', 'type': 'ticket_purchase', 'guest_name': is_guest_ticket_payment.name, 'qr_image_url': is_guest_ticket_payment.qr_image_url})
            else:
                delete_pix_qr_code_file(is_guest_ticket_payment)
                return jsonify({'status': 'PAID', 'message': 'Pagamento já processado.'})

        elif status == 'EXPIRED':
            if is_guest_ticket_payment.payment_status in ['pending', 'pending_owner_invite']:
                is_guest_ticket_payment.payment_status = 'failed'
                db.session.commit()
            return jsonify({'status': 'EXPIRED'})

        return jsonify({'status': status})
    except Exception as e:
        current_app.logger.error(f"Erro ao verificar status do pagamento via API: {e}")
        return jsonify({'status': 'error', 'message': 'Erro ao consultar status do pagamento.'}), 500

@bp.route('/webhooks/abacatepay', methods=['POST'])
def abacatepay_webhook():
    payload = request.get_json()
    current_app.logger.info(f"Webhook recebido: {payload}")

    if not payload or 'event' not in payload or 'data' not in payload:
        current_app.logger.warning("Webhook AbacatePay com payload inválido.")
        return jsonify({'status': 'error', 'message': 'Invalid payload'}), 400

    event_type = payload.get('event')
    transaction_id = payload.get('data', {}).get('id')

    if event_type == 'pix_qr_code.paid' and transaction_id:
        guest = Guest.query.filter_by(payment_charge_id=transaction_id).first()
        if guest and guest.payment_status != 'paid':
            guest.payment_status = 'paid'
            if guest.purchase_price is None:
                guest.purchase_price = guest.party.ticket_price
            db.session.commit()
            delete_pix_qr_code_file(guest)
            current_app.logger.info(f"Pagamento de ingresso confirmado via webhook para {guest.name} (Festa: {guest.party.name})")
            return jsonify({'status': 'success', 'message': 'Guest ticket status updated'}), 200
        elif guest:
            current_app.logger.info(f"Pagamento webhook recebido para ingresso já pago: {guest.name}")
            delete_pix_qr_code_file(guest)
            return jsonify({'status': 'success', 'message': 'Ticket already paid'}), 200

    current_app.logger.info(f"Webhook processado (evento não relevante ou ID não encontrado): {event_type}, {transaction_id}")
    return jsonify({'status': 'received', 'message': 'Event not processed or already handled'}), 200
