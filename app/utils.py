import os
import io
import base64
import colorsys
import urllib.parse
import random
import string
import requests
import qrcode
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from datetime import datetime
from PIL import Image, ImageDraw, ImageFont as PILImageFont, ImageOps
from flask import current_app
from fpdf import FPDF, XPos, YPos

from app.config import Config
from app.extensions import db
from app.services.storage import get_storage_service

# Import models inside functions or use strings to avoid circular imports? 
# Usually strictly typing requires models. But Python is dynamic.
# I will import models inside functions where necessary or use argument passing style.

def save_base64_as_png(base64_string, charge_id):
    """Decodifica uma string base64 e a envia para o S3."""
    if not base64_string or not charge_id:
        return None
    try:
        if "data:image/png;base64," in base64_string:
            clean_base64_string = base64_string.split(",")[1]
        else:
            clean_base64_string = base64_string
        img_data = base64.b64decode(clean_base64_string)
        
        # Cria objeto em memória para upload
        img_io = io.BytesIO(img_data)
        filename = f"{charge_id}.png"
        
        storage = get_storage_service()
        # Salva na pasta payment_qrcodes dentro do bucket
        full_key = storage.upload_file(
            img_io, 
            filename, 
            folder=Config.PAYMENT_QRCODES_FOLDER_NAME,
            content_type="image/png"
        )
        
        if full_key:
            return filename # Retornamos apenas o nome do arquivo para manter compatibilidade com o banco
        return None
    except Exception as e:
        current_app.logger.error(f"Erro ao salvar imagem base64 no S3 para charge_id {charge_id}: {e}")
        return None

def delete_pix_qr_code_file(guest):
    """Deleta o arquivo de imagem do QR Code do PIX associado a um convidado do S3."""
    if guest and guest.pix_qr_code_filename:
        try:
            storage = get_storage_service()
            folder = Config.PAYMENT_QRCODES_FOLDER_NAME
            # O banco guarda 'filename.png', mas no S3 está em 'payment_qrcodes/filename.png'
            key = f"{folder}/{guest.pix_qr_code_filename}"
            if storage.delete_file(key):
                current_app.logger.info(f"Arquivo QR Code PIX {key} deletado do S3 com sucesso.")
            else:
                current_app.logger.warning(f"Falha ao deletar {key} do S3.")
        except Exception as e:
            current_app.logger.error(f"Erro ao deletar o arquivo QR Code PIX {guest.pix_qr_code_filename}: {e}")

def get_vibrant_colors(pil_img, num_colors=2):
    img = pil_img.copy().convert("RGBA")
    img.thumbnail((100, 100))
    all_colors = img.getcolors(img.size[0] * img.size[1])
    if not all_colors: return []
    vibrant_candidates = []
    for count, rgba in all_colors:
        r, g, b, a = rgba
        if a < 128: continue
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s > 0.35 and 0.3 < v < 0.95:
            score = (s * 0.8) + (count / (img.size[0] * img.size[1]) * 0.2)
            vibrant_candidates.append({'color': (r, g, b), 'score': score})
    if not vibrant_candidates: return []
    vibrant_candidates.sort(key=lambda x: x['score'], reverse=True)
    return [c['color'] for c in vibrant_candidates[:num_colors]]

def generate_qr_code_image(qr_data, guest_name, party, output_format='PNG', font_override=None):
    try:
        CARD_WIDTH, PADDING, SPACING, LOGO_ASPECT_RATIO = 600, 40, 25, 2 / 1
        GRADIENT_START, GRADIENT_END = (15, 23, 42), (59, 130, 246)
        CARD_BG_COLOR, TEXT_COLOR, FOOTER_COLOR = (255, 255, 255, 235), (15, 23, 42), (100, 116, 139)
        GUEST_NAME_COLOR = (37, 99, 235)

        try:
            # Use a fonte selecionada para a festa
            selected_font_name = font_override if font_override else (party.invite_font if party.invite_font else 'Montserrat-Regular')
            # Assuming 'static' is in current_app.root_path (app/static)
            font_path = os.path.join(current_app.root_path, 'static', 'fonts', f"{selected_font_name}.ttf")
            
            current_app.logger.info(f"Attempting to load font from: {font_path}")

            font_party_name = PILImageFont.truetype(font_path, 52)
            font_guest_name = PILImageFont.truetype(font_path, 36)
            font_footer_path = os.path.join(current_app.root_path, "static", "fonts", "Montserrat-Regular.ttf")
            font_footer = PILImageFont.truetype(font_footer_path, 14) # Footer always Montserrat
        except IOError:
            current_app.logger.warning(f"Fonte personalizada {selected_font_name} não encontrada. Usando fonte padrão.")
            font_party_name, font_guest_name, font_footer = (PILImageFont.load_default(s) for s in [52,36,14])

        logo_img_raw, logo_height = None, 0
        if party.logo_filename:
            try:
                # Load logo from S3
                storage = get_storage_service()
                key = f"{Config.PARTY_LOGOS_FOLDER_NAME}/{party.logo_filename}"
                
                # Download image bytes from S3
                response = storage.s3_client.get_object(Bucket=storage.bucket_name, Key=key)
                logo_bytes = response['Body'].read()
                
                logo_height = int(CARD_WIDTH / LOGO_ASPECT_RATIO)
                logo_img_raw = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
                try:
                    vibrant_colors = get_vibrant_colors(logo_img_raw, num_colors=2)
                    if len(vibrant_colors) >= 1:
                        main_color = vibrant_colors[0]
                        GUEST_NAME_COLOR, GRADIENT_END = main_color, main_color
                        GRADIENT_START = tuple(int(c * 0.5) for c in main_color)
                except Exception as e:
                    current_app.logger.warning(f"Não foi possível extrair cores: {e}.")
            except Exception as e:
                current_app.logger.warning(f"Falha ao carregar logo do S3: {e}")

        qr_instance = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
        qr_instance.add_data(qr_data)
        qr_instance.make(fit=True)
        img_qr = qr_instance.make_image(fill_color="black", back_color="white").convert('RGB')

        content_height = PADDING + font_party_name.getbbox(party.name)[3] + SPACING + img_qr.size[0] + SPACING + font_guest_name.getbbox(guest_name)[3] + SPACING + font_footer.getbbox("Feito com QRPass")[3] + PADDING
        card_height = logo_height + content_height
        canvas_height, canvas_width = card_height + PADDING * 2, CARD_WIDTH + PADDING * 2

        canvas = Image.new('RGB', (canvas_width, canvas_height), GRADIENT_START)
        draw = ImageDraw.Draw(canvas)
        for y in range(canvas_height):
            ratio = y / canvas_height
            color_tuple = tuple(int(start * (1 - ratio) + end * ratio) for start, end in zip(GRADIENT_START, GRADIENT_END))
            draw.line([(0, y), (canvas_width, y)], fill=color_tuple)

        card_img = Image.new('RGBA', (CARD_WIDTH, card_height), (0,0,0,0))
        ImageDraw.Draw(card_img).rounded_rectangle((0, 0, CARD_WIDTH, card_height), radius=30, fill=CARD_BG_COLOR)

        if logo_img_raw:
            logo_fitted = ImageOps.fit(logo_img_raw, (CARD_WIDTH, logo_height), Image.Resampling.LANCZOS)
            mask = Image.new('L', logo_fitted.size, 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle((0, 0, *logo_fitted.size), radius=30, fill=255)
            mask_draw.rectangle((0, 30, *logo_fitted.size), fill=255)
            card_img.paste(logo_fitted, (0, 0), mask)

        content_draw = ImageDraw.Draw(card_img)
        current_y = logo_height + PADDING
        items = [(party.name, font_party_name, TEXT_COLOR), (img_qr, None, None), (guest_name, font_guest_name, GUEST_NAME_COLOR), ("Feito com QRPass", font_footer, FOOTER_COLOR)]
        for item, font, color in items:
            if isinstance(item, Image.Image):
                card_img.paste(item, ((CARD_WIDTH - item.width) // 2, int(current_y)))
                current_y += item.height + SPACING
            else:
                bbox = content_draw.textbbox((0, 0), item, font=font)
                content_draw.text(((CARD_WIDTH - bbox[2]) / 2, current_y), item, font=font, fill=color)
                current_y += bbox[3] + SPACING

        canvas.paste(card_img, ((canvas_width - CARD_WIDTH) // 2, (canvas_height - card_height) // 2), card_img)

        img_io = io.BytesIO()
        canvas.save(img_io, format=output_format)
        img_io.seek(0)
        return img_io

    except Exception as e:
        current_app.logger.error(f"Erro ao gerar imagem do QR Code: {e}")
        return None

def generate_unique_code(model, field, length=8, chars=string.ascii_uppercase + string.digits):
    while True:
        code = ''.join(random.choices(chars, k=length))
        if not db.session.query(model).filter(getattr(model, field) == code).first():
            return code

def create_abacatepay_charge(amount, description, customer_info):
    url = "https://api.abacatepay.com/v1/pixQrCode/create"
    headers = {"Authorization": f"Bearer {Config.ABACATE_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "amount": int(amount * 100),
        "expiresIn": 3600,
        "description": description,
        "customer": customer_info
    }
    current_app.logger.info(f"Chamando AbacatePay create com payload: {payload}")
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json().get('data', {})
    if not all([data.get('brCodeBase64'), data.get('brCode'), data.get('id')]):
        raise ValueError("Resposta da API de PIX incompleta ou sem dados do QR Code.")
    return data

def check_abacatepay_status(charge_id):
    url = "https://api.abacatepay.com/v1/pixQrCode/check"
    headers = {"Authorization": f"Bearer {Config.ABACATE_API_KEY}"}
    params = {"id": charge_id}
    current_app.logger.info(f"Chamando AbacatePay check para charge_id: {charge_id}")
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    data = response.json().get('data', {})
    if not data.get('status'):
        raise ValueError("Resposta da API de PIX incompleta (status ausente).")
    return data

def generate_google_maps_url(location_query):
    if not location_query: return None
    base_url = "https://www.google.com/maps/search/?api=1&query="
    return base_url + urllib.parse.quote_plus(location_query)

def get_party_stats_data(party_id):
    # Avoid circular import
    from app.models import Guest, Party
    
    total_invited = Guest.query.filter_by(party_id=party_id).count()
    entered_count = Guest.query.filter_by(party_id=party_id, entered=True).count()
    not_entered_count = total_invited - entered_count
    
    # This might fail if called without valid context or session, but should be fine in requests
    total_revenue = db.session.query(db.func.sum(Guest.purchase_price)).filter(
        Guest.party_id == party_id,
        Guest.payment_status == 'paid'
    ).scalar() or 0.0

    total_paid_tickets = Guest.query.filter_by(party_id=party_id, payment_status='paid').count()

    return {
        'total_invited': total_invited,
        'total_paid_tickets': total_paid_tickets,
        'entered_count': entered_count,
        'not_entered_count': not_entered_count,
        'percentage_entered': round((entered_count / total_invited) * 100, 2) if total_invited > 0 else 0.0,
        'total_revenue': total_revenue
    }

def get_all_guests_for_export(party_id):
    from app.models import Guest
    return Guest.query.filter_by(party_id=party_id).all()


class PDF(FPDF):
    def __init__(self, orientation='P', unit='mm', format='A4', party_name=''):
        super().__init__(orientation, unit, format)
        self.montserrat_font_path = Config.FONT_PATH
        self.font_name = 'Montserrat'
        self.default_font = 'Helvetica'
        self.current_font_family = self.default_font
        self.party_name = party_name
        if os.path.exists(self.montserrat_font_path):
            try:
                self.add_font(self.font_name, '', self.montserrat_font_path)
                self.add_font(self.font_name, 'B', self.montserrat_font_path)
                self.add_font(self.font_name, 'I', self.montserrat_font_path)
                self.current_font_family = self.font_name
            except Exception:
                current_app.logger.warning(f"Erro ao carregar fonte Montserrat de {self.montserrat_font_path}. Usando fonte padrão.")
                pass
    def header(self):
        self.set_font(self.current_font_family, 'B', 16)
        title, title_w = f'Relatório do Evento: {self.party_name}', self.get_string_width(f'Relatório do Evento: {self.party_name}')
        self.set_x((self.w - title_w) / 2)
        self.cell(title_w, 10, title, border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font(self.current_font_family, 'I', 8)
        # Adicionar número da página
        self.cell(0, 10, f'Página {self.page_no()}/{{nb}}', border=0, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
        # Adicionar data de geração
        self.set_y(-10)
        self.set_font(self.current_font_family, '', 7)
        self.cell(0, 5, f'Gerado em: {datetime.now(Config.BRASILIA_TZ).strftime("%d/%m/%Y %H:%M:%S")}', border=0, align='R')
    def draw_stats_summary(self, stats_data):
        self.set_font(self.current_font_family, 'B', 11)
        self.cell(0, 10, "Estatísticas do evento", border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
        self.ln(1)
        col_width_stat = (self.w - self.l_margin - self.r_margin) / 4.5
        stat_box_height = 14
        line_height_label, line_height_value = 5, 6
        padding_top_box = (stat_box_height - (line_height_label + line_height_value)) / 2

        current_x, base_y = self.l_margin, self.get_y()

        stats_items = [
            ("Convidados (Total):", str(stats_data['total_invited'])),
            ("Ingressos Pagos:", str(stats_data['total_paid_tickets'])),
            ("Entraram:", str(stats_data['entered_count'])),
            ("Não Entraram:", str(stats_data['not_entered_count'])),
            ("Comparecimento:", f"{stats_data['percentage_entered']:.2f}%"),
            ("Faturamento Total:", f"R$ {stats_data['total_revenue']:.2f}")
        ]

        for i, (label, value) in enumerate(stats_items):
            self.set_fill_color(240, 240, 240)
            self.rect(current_x, base_y, col_width_stat, stat_box_height, 'F')
            self.set_draw_color(200, 200, 200)
            self.rect(current_x, base_y, col_width_stat, stat_box_height, 'D')

            y_pos_label = base_y + padding_top_box
            self.set_xy(current_x, y_pos_label)
            self.set_text_color(0, 0, 0)
            self.set_font(self.current_font_family, '', 8.5)
            self.cell(col_width_stat, line_height_label, label, border=0, align='C')

            y_pos_value = y_pos_label + line_height_label
            self.set_xy(current_x, y_pos_value)
            self.set_text_color(0, 100, 200)
            if label == "Faturamento Total:":
                self.set_text_color(0, 150, 0)
            self.set_font(self.current_font_family, 'B', 10)
            self.cell(col_width_stat, line_height_value, value, border=0, align='C')
            self.set_text_color(0, 0, 0)

            current_x += col_width_stat
            if (i + 1) % 4 == 0 and (i + 1) < len(stats_items):
                current_x = self.l_margin
                base_y += stat_box_height + 5
                self.ln(5)
        self.set_y(base_y + stat_box_height)
        self.ln(5)


    def draw_pie_chart(self, stats_data, y_offset, chart_size_mm=90):
        labels, sizes, colors = ('Entraram', 'Não Entraram'), [stats_data['entered_count'], stats_data['not_entered_count']], ['#4BC0C0', '#FF6384']

        if sum(sizes) == 0:
            self.set_font(self.current_font_family, 'I', 10)
            self.cell(0, 10, "Sem dados de comparecimento de ingressos pagos para o gráfico.", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            self.ln(5)
            return

        fig, ax = plt.subplots(figsize=(chart_size_mm / 25.4, chart_size_mm / 25.4), dpi=100)
        wedges, _, autotexts = ax.pie(sizes, explode=(0.05, 0) if sizes[0] > 0 else (0,0), labels=None, colors=colors, autopct='%1.1f%%', shadow=False, startangle=90)
        for autotext in autotexts: autotext.set_color('white'); autotext.set_fontweight('bold')
        ax.axis('equal')
        ax.legend(wedges, [f'{l} ({s})' for l, s in zip(labels, sizes)], loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))

        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', bbox_inches='tight', transparent=True, pad_inches=0)
        plt.close(fig)

        self.image(img_buffer, x=(self.w - chart_size_mm) / 2, y=self.get_y(), w=chart_size_mm)
        self.set_y(self.get_y() + chart_size_mm + 5)

    def chapter_body(self, guests_data_table):
        self.set_font(self.current_font_family, 'B', 11)
        self.cell(0, 10, "Lista de Convidados", border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        self.ln(1)

        self.set_font(self.current_font_family, 'B', 9)
        self.set_fill_color(230, 230, 230)

        col_widths = {"name": 55, "payment_status": 22, "purchase_price": 20, "purchased_by": 28, "status": 20, "check_in": 30, "added_by": 25}
        total_width = sum(col_widths.values())
        start_x = self.l_margin + (self.w - 2 * self.l_margin - total_width) / 2

        self.set_x(start_x)
        self.cell(col_widths["name"], 8, 'Nome', 1, 0, 'C', 1)
        self.cell(col_widths["payment_status"], 8, 'Pagamento', 1, 0, 'C', 1)
        self.cell(col_widths["purchase_price"], 8, 'Preço Pago', 1, 0, 'C', 1)
        self.cell(col_widths["purchased_by"], 8, 'Comprado Por', 1, 0, 'C', 1)
        self.cell(col_widths["status"], 8, 'Entrou?', 1, 0, 'C', 1)
        self.cell(col_widths["check_in"], 8, 'Data Check-in', 1, 0, 'C', 1)
        self.cell(col_widths["added_by"], 8, 'Adicionado Por', 1, 1, 'C', 1)

        self.set_font(self.current_font_family, '', 8.5)
        for i, guest_obj in enumerate(guests_data_table):
            self.set_x(start_x)
            fill = i % 2 == 0

            payment_status_display = {
                'not_applicable': 'Gratuito',
                'pending_owner_invite': 'Aguard. Pgto.',
                'pending': 'Aguard. Pgto.',
                'paid': 'Pago',
                'failed': 'Falhou'
            }.get(guest_obj.payment_status, guest_obj.payment_status)

            purchase_price_display = f"R$ {guest_obj.purchase_price:.2f}" if guest_obj.purchase_price is not None else 'N/A'
            purchased_by_name = guest_obj.purchaser.username if guest_obj.purchaser else 'N/A'
            added_by_name = guest_obj.adder.username if guest_obj.adder else 'N/A'

            self.cell(col_widths["name"], 7, guest_obj.name, 1, 0, 'L', fill)
            self.cell(col_widths["payment_status"], 7, payment_status_display, 1, 0, 'C', fill)
            self.cell(col_widths["purchase_price"], 7, purchase_price_display, 1, 0, 'C', fill)
            self.cell(col_widths["purchased_by"], 7, purchased_by_name, 1, 0, 'C', fill)
            self.cell(col_widths["status"], 7, 'Sim' if guest_obj.entered else 'Não', 1, 0, 'C', fill)
            self.cell(col_widths["check_in"], 7, guest_obj.get_check_in_time_str(), 1, 0, 'C', fill)
            self.cell(col_widths["added_by"], 7, added_by_name, 1, 1, 'C', fill)
