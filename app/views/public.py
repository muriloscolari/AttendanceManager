from flask import Blueprint, render_template, make_response, request, flash, redirect, url_for
from app.models import Party
from app.utils import get_party_stats_data

bp = Blueprint('public', __name__)

@bp.route('/p/<shareable_link_id>')
def public_party_page(shareable_link_id):
    party = Party.query.filter_by(shareable_link_id=shareable_link_id).first_or_404()
    stats = get_party_stats_data(party.id)
    return render_template('public_party_card.html', party=party, stats=stats)

@bp.route('/scanner', methods=['GET', 'POST'])
def public_scanner():
    if request.method == 'POST':
        party_code_input = request.form.get('party_code', '').upper()
        party = Party.query.filter_by(party_code=party_code_input).first()
        if party:
            resp = make_response(render_template('scanner.html', party=party))
            resp.set_cookie('party_code', party_code_input, max_age=30*24*60*60)
            return resp
        else:
            flash('Código da festa inválido.', 'danger')
    party_code_from_cookie = request.cookies.get('party_code')
    if party_code_from_cookie:
        party = Party.query.filter_by(party_code=party_code_from_cookie).first()
        if party:
            return render_template('scanner.html', party=party)
    return render_template('scanner_login.html')

@bp.route('/scanner/forget')
def forget_scanner():
    resp = make_response(redirect(url_for('public.public_scanner')))
    resp.set_cookie('party_code', '', expires=0)
    flash('Você se desconectou do scanner.', 'info')
    return resp
