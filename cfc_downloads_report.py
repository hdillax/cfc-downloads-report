# webapp.py (com correção de caracteres especiais)
import streamlit as st
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from fpdf import FPDF
from fpdf.enums import Align
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import os

# --- PWA SETTINGS ---
APP_NAME = "Downloads Report"
APP_ICON = "🦉"  # An emoji, which we'll turn into an SVG icon

# 1. Create the SVG icon from the emoji
svg_icon = f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
    <text y=".9em" font-size="90">{APP_ICON}</text>
</svg>
"""
# URL-encode the SVG
encoded_svg_icon = "data:image/svg+xml," + quote(svg_icon)

# 2. Create the manifest as a Python dictionary
manifest = {
    "name": APP_NAME,
    "short_name": APP_NAME,
    "icons": [
        {
            "src": encoded_svg_icon,
            "sizes": "192x192",
            "type": "image/svg+xml",
        }
    ],
    "theme_color": "#ffffff",
    "background_color": "#ffffff",
    "start_url": ".",
    "display": "standalone",
    "scope": "/",
}

# 3. Create the minimal service worker script
service_worker = """
self.addEventListener('fetch', function(event) {});
"""

# 4. Generate the HTML to inject
pwa_html = f"""
    <link rel="manifest" href="data:application/manifest+json,{quote(json.dumps(manifest))}">
    <script>
        var sw_content = `{service_worker}`;
        var sw_blob = new Blob([sw_content], {{type: 'application/javascript'}});
        var sw_url = URL.createObjectURL(sw_blob);

        if ('serviceWorker' in navigator) {{
            navigator.serviceWorker.register(sw_url).then(function(reg) {{
                console.log('Service Worker registered.', reg);
            }}).catch(function(err) {{
                console.log('Service Worker registration failed:', err);
            }});
        }}
    </script>
"""

# --- INJECT PWA HTML ---
st.html(pwa_html)

# --- Suas chaves/secrets devem ser configuradas no Streamlit Cloud ---
KEY = st.secrets["SENDOWL_KEY"]
SECRET = st.secrets["SENDOWL_SECRET"]
BASE_URL = "https://www.sendowl.com/api/v1_3"
TIMEOUT = int(os.getenv("SENDOWL_TIMEOUT", "45"))

session = requests.Session()
retry = Retry(total=4, backoff_factor=0.6, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retry))

# --- NOVA FUNÇÃO PARA EVITAR ERROS DE ENCODING NO PDF ---
def sanitize_text(text: str) -> str:
    """
    Remove caracteres incompatíveis com a fonte padrão do FPDF (latin-1),
    substituindo-os por '?' para evitar que a geração do PDF falhe.
    """
    if not text:
        return ""
    return text.encode("latin-1", "replace").decode("latin-1")

# --- Funções de Lógica de Negócio ---
@st.cache_data(show_spinner=False)
def _get(path: str, params: Dict[str, Any] | None = None) -> Any:
    r = session.get(
        f"{BASE_URL}{path}",
        auth=(KEY, SECRET),
        headers={"Accept": "application/json"},
        params=params,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json() if r.content.strip() else None

def _fmt(ts: str) -> str:
    if not ts:
        return "N/A"
    try:
        brasilia_tz = timezone(timedelta(hours=-3))
        utc_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        brasilia_time = utc_time.astimezone(brasilia_tz)
        return brasilia_time.strftime("%d/%m/%Y %H:%M:%S")
    except (ValueError, TypeError):
        return str(ts)

def _normalize_orders(p: Any) -> List[Dict[str, Any]]:
    if p is None:
        return []
    if isinstance(p, list):
        return [o.get("order", o) for o in p]
    if isinstance(p, dict):
        return [o.get("order", o) for o in p.get("orders", [])]
    raise TypeError("Formato inesperado em /orders/search")

def search_orders(email: str) -> List[Dict[str, Any]]:
    return _normalize_orders(_get("/orders/search", params={"email": email}))

@st.cache_data(show_spinner=False)
def get_order_details(order_id: int) -> Dict[str, Any]:
    return _get(f"/orders/{order_id}")["order"]

_product_cache: Dict[int, str] = {}
def _product_name(pid: int | None) -> str:
    if pid is None:
        return "(produto desconhecido)"
    if pid in _product_cache:
        return _product_cache[pid]
    try:
        name = _get(f"/products/{pid}").get("product", {}).get("name", str(pid))
    except Exception:
        name = f"(ID: {pid})"
    _product_cache[pid] = name
    return name

class PDFWithWatermark(FPDF):
    def header(self):
        prev_font_family, prev_font_style, prev_font_size = (
            self.font_family,
            self.font_style,
            self.font_size_pt,
        )
        self.set_font("helvetica", "B", 85)
        self.set_text_color(230, 230, 230)
        with self.rotation(angle=45, x=self.w / 2, y=self.h / 2):
            text_width = self.get_string_width("CONFIDENCIAL")
            self.text(
                x=(self.w - text_width) / 2,
                y=(self.h / 2) + 15,
                text="CONFIDENCIAL",
            )
        self.set_font(prev_font_family, prev_font_style, prev_font_size)
        self.set_text_color(0, 0, 0)

def generate_pdf_bytes(order: Dict[str, Any], downloads: List[Dict[str, Any]], order_name_str: str) -> bytes:
    pdf = PDFWithWatermark()
    pdf.add_page()
    pdf.set_draw_color(215, 215, 215)

    # Sanitize all text inputs before adding them to the PDF
    safe_order_name = sanitize_text(order_name_str)
    safe_buyer_email = sanitize_text(str(order.get("buyer_email") or "N/A"))
    safe_buyer_ip = sanitize_text(str(order.get("buyer_ip_address") or order.get("buyer_ip") or "N/A"))

    pdf.set_font("helvetica", "B", 20)
    pdf.cell(0, 10, f"Relatorio do Pedido: {safe_order_name}", align=Align.C, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Dados do Pedido", new_x="LMARGIN", new_y="NEXT")

    order_data = [
        ["ID do Pedido:", str(order.get("id", "N/A"))],
        ["Data da Compra:", _fmt(order.get("created_at"))],
        ["E-mail do Comprador:", safe_buyer_email],
        ["IP da Compra:", safe_buyer_ip],
    ]

    pdf.set_font("helvetica", size=10)
    for row in order_data:
        pdf.set_font(style="B")
        pdf.cell(45, 8, row[0], border=1)
        pdf.set_font(style="")
        pdf.multi_cell(0, 8, row[1], border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Análise para Cancelamento", new_x="LMARGIN", new_y="NEXT")

    valor_raw = order.get("settled_gross") or order.get("settled_gross_cents", 0)
    valor_pago = float(valor_raw) / 100 if isinstance(valor_raw, int) else float(str(valor_raw))
    download_items = order.get("download_items") or []
    total_files = len({str(item.get("file_id", item.get("id", item))) for item in download_items})
    baixados = len({str(dl.get("file_id", dl.get("id", dl.get("product_id")))) for dl in downloads})
    faltantes = max(total_files - baixados, 0)
    reembolso_ratio = f"{faltantes} / {total_files}" if total_files else "0 / 0"
    valor_reembolso = (valor_pago * faltantes / total_files) if total_files else 0.0

    cancel_data = [
        ["Valor Pago:", f"R$ {valor_pago:.2f}"],
        ["Arquivos Totais:", str(total_files)],
        ["Arquivos Baixados:", str(baixados)],
        ["Proporção para Reembolso:", reembolso_ratio],
        ["Valor Reembolso:", f"R$ {valor_reembolso:.2f}"],
    ]

    pdf.set_font("helvetica", size=10)
    for i, row in enumerate(cancel_data):
        pdf.set_font(style="B")
        pdf.cell(60, 8, row[0], border=1)
        pdf.set_font(style="B" if i == len(cancel_data) - 1 else "")
        pdf.multi_cell(0, 8, row[1], border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Histórico de Downloads", new_x="LMARGIN", new_y="NEXT")

    if not downloads:
        pdf.set_font("helvetica", "", 10)
        pdf.cell(0, 8, "Nenhum download registrado para este pedido.")
    else:
        headers = ["#", "Nome do Produto", "Data do Download"]
        col_widths = (15, 115, 60)
        pdf.set_font("helvetica", "B", 10)
        pdf.set_fill_color(210, 210, 210)
        for i, header in enumerate(headers):
            pdf.cell(col_widths[i], 8, header, border=1, fill=True, align=Align.C)
        pdf.ln()
        pdf.set_font("helvetica", "", 9)
        for i, dl in enumerate(downloads):
            # --- BUG CORRIGIDO + SANITIZAÇÃO ---
            product_name_raw = str(dl.get("product_name") or _product_name(dl.get("product_id")) or "(Nome desconhecido)")
            product_name = sanitize_text(product_name_raw)
            row = [str(i + 1), product_name, _fmt(dl.get("created_at"))]
            pdf.cell(col_widths[0], 8, row[0], border=1, align=Align.C)
            pdf.cell(col_widths[1], 8, row[1], border=1)
            pdf.cell(col_widths[2], 8, row[2], border=1, align=Align.C)
            pdf.ln()

    # <-- FIX: garante bytes para o streamlit.download_button em qualquer versão do fpdf2
    out = pdf.output(dest="S")
    if isinstance(out, (bytes, bytearray, memoryview)):
        return bytes(out)
    elif isinstance(out, str):
        # Algumas versões antigas retornam str (latin-1)
        return out.encode("latin-1", "ignore")
    else:
        return bytes(out)

# --- Interface do Aplicativo Web com Streamlit ---
st.set_page_config(page_title="Downloads Report", layout="centered",page_icon="🦉", initial_sidebar_state="collapsed")
st.subheader("🦉 Downloads Report")

if "orders" not in st.session_state:
    st.session_state.orders = []
if "email" not in st.session_state:
    st.session_state.email = ""

def reset_search():
    st.session_state.orders = []
    st.session_state.email = ""

with st.form(key="search_form"):
    email_input = st.text_input("E-mail", value=st.session_state.email)
    submit_button = st.form_submit_button(label="Buscar")

if submit_button:
    if not email_input or "@" not in email_input:
        st.error("Por favor, insira um e-mail válido.")
    else:
        st.session_state.email = email_input
        try:
            with st.spinner("Buscando pedidos..."):
                st.session_state.orders = search_orders(st.session_state.email)
            if not st.session_state.orders:
                st.warning("Nenhum pedido encontrado para este e-mail.")
        except Exception as e:
            st.error(f"Ocorreu um erro de API: {e}")

if st.session_state.orders:
    st.markdown("---")
    st.subheader(f"Pedidos encontrados para: {st.session_state.email}")

    for i, order in enumerate(st.session_state.orders):
        # separador entre os cards (não antes do primeiro)
        if i > 0:
            st.markdown("---")   # ou: st.divider()

        order_id = order["id"]
        order_name = order.get("order_name", f"#{order_id}")
        order_date = _fmt(order.get("created_at", "")).split(" ")[0]

        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**Pedido:** {sanitize_text(order_name)}")
                st.caption(f"**Data:** {order_date} | **ID:** {order_id}")
            with col2:
                st.write("")
                if st.button("Gerar PDF", key=f"btn_{order_id}"):
                    try:
                        with st.spinner(f"Gerando relatório para {sanitize_text(order_name)}..."):
                            details = get_order_details(order_id)
                            downloads = details.get("downloads", [])
                            pdf_bytes = generate_pdf_bytes(details, downloads, order_name)

                            safe_name = sanitize_text(str(order_name)).replace("#", "").replace("/", "-")
                            today = datetime.now().strftime("%Y-%m-%d")
                            filename = f"Relatorio_Pedido_{safe_name}_{today}.pdf"

                            st.download_button(
                                label="Clique para Baixar",
                                data=pdf_bytes,  # bytes garantidos
                                file_name=filename,
                                mime="application/pdf",
                                key=f"dl_{order_id}",
                            )
                    except Exception as e:
                        st.error(f"Erro ao gerar PDF: {e}")

    st.markdown("---")
    st.button("Nova Consulta", on_click=reset_search)







