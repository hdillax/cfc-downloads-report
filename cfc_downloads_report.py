# Relatório de downloads
# Versão: 2025-09-30 — Correções e robustez:
# - PDF: retorna sempre 'bytes' (st.download_button aceita bytes/str/file-like)
# - Sanitize de texto Latin-1 seguro (core fonts do FPDF não são unicode)
# - Try/except com mensagens claras no Streamlit
# - Timeouts, validações, e pequenos ajustes de UX

from __future__ import annotations

import os
import base64
import json
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
import requests
import pandas as pd
import streamlit as st

# fpdf2: usamos Align com fallback para versões antigas
try:
    from fpdf import FPDF
    try:
        from fpdf.enums import Align
    except Exception:
        class Align:
            L = "L"; C = "C"; R = "R"; J = "J"
except Exception as e:
    raise RuntimeError(
        "A biblioteca 'fpdf2' é necessária. Adicione 'fpdf2' no requirements.txt"
    ) from e


# =========================
# Configuração básica
# =========================
SENDOWL_API_BASE = "https://www.sendowl.com/api/v1"
DEFAULT_TIMEOUT = (10, 30)  # (connect, read) em segundos

st.set_page_config(page_title="Relatório de Downloads — SendOwl", page_icon="📄", layout="wide")


# =========================
# Utilitários
# =========================
def _fmt_dt_iso8601(value: Any) -> str:
    """Tenta formatar datas do SendOwl para 'YYYY-MM-DD HH:MM' local."""
    if not value:
        return "N/A"
    try:
        # SendOwl costuma devolver ISO8601 com Z/offset
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Converte para local automaticamente via timezone local do servidor/Streamlit
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        # fallback plain
        return str(value)


def _coalesce(*vals, default: Any = "N/A") -> Any:
    for v in vals:
        if v is not None and v != "":
            return v
    return default


def sanitize_text(text: Any) -> str:
    """
    Garante compatibilidade com fontes core do FPDF (Latin-1).
    Converte para str, faz encode/decode latin-1 com replace (sem quebrar PDF).
    """
    if text is None:
        return ""
    try:
        s = str(text)
        # Normaliza para latin-1 seguro (core fonts)
        return s.encode("latin-1", "replace").decode("latin-1")
    except Exception:
        return str(text)


def _format_currency_brl(value: Any) -> str:
    try:
        v = float(str(value))
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(value)


def _make_auth_header(api_key: str, api_secret: str) -> Dict[str, str]:
    token = f"{api_key}:{api_secret}".encode("utf-8")
    b64 = base64.b64encode(token).decode("ascii")
    return {"Authorization": f"Basic {b64}", "Accept": "application/json"}


# =========================
# API Client (SendOwl)
# =========================
def fetch_order(order_id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """
    GET /api/v1/orders/:id.json
    """
    url = f"{SENDOWL_API_BASE}/orders/{order_id}.json"
    r = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    if r.status_code == 404:
        raise ValueError(f"Pedido {order_id} não encontrado no SendOwl.")
    r.raise_for_status()
    data = r.json()
    # algumas contas retornam em chaves diferentes; padronizamos um nível
    # Ex.: {"order": {...}} ou já o objeto
    if isinstance(data, dict) and "order" in data:
        return data["order"]
    return data


def fetch_order_downloads(order_id: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    GET /api/v1/orders/:id/downloads.json
    (em algumas contas/versões pode ser /orders/:id/downloads)
    """
    url = f"{SENDOWL_API_BASE}/orders/{order_id}/downloads.json"
    r = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    if r.status_code == 404:
        # Sem downloads ou rota não disponível
        return []
    r.raise_for_status()
    data = r.json()
    # alguns retornam {"downloads":[...]} — padronizamos lista
    if isinstance(data, dict) and "downloads" in data:
        return data["downloads"]
    if isinstance(data, list):
        return data
    return []


# =========================
# PDF
# =========================
class PDFWithBrand(FPDF):
    def header(self):
        self.set_auto_page_break(auto=True, margin=15)
        self.set_font("helvetica", "B", 12)
        self.set_text_color(40, 40, 40)
        self.cell(0, 10, "Concurseiro Fora da Caixa — Relatório de Downloads", ln=1, align=Align.L)
        self.ln(2)
        self.set_draw_color(220, 220, 220)
        self.set_line_width(0.4)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Gerado em {datetime.now().strftime('%Y-%m-%d %H:%M')} — Página {self.page_no()}", align=Align.C)

    def table_row(self, cols: List[Tuple[str, int]], bold_first: bool = True, borders: bool = True):
        """
        cols: lista [(texto, largura_mm), ...]
        """
        self.set_font("helvetica", "B" if bold_first else "", 10)
        for i, (txt, w) in enumerate(cols):
            t = sanitize_text(txt)
            border = 1 if borders else 0
            if i == 0 and bold_first:
                self.set_font("helvetica", "B", 10)
            else:
                self.set_font("helvetica", "", 10)
            self.cell(w, 8, t, border=border)
        self.ln(8)


def generate_pdf_bytes(order: Dict[str, Any], downloads: List[Dict[str, Any]], order_name_str: str) -> bytes:
    """
    Gera o PDF e retorna SEMPRE 'bytes'.
    (st.download_button NÃO aceita bytearray)
    """
    pdf = PDFWithBrand(orientation="P", unit="mm", format="A4")
    pdf.add_page()

    # Título
    pdf.set_font("helvetica", "B", 18)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, sanitize_text(f"Relatório do Pedido: {order_name_str}"), ln=1, align=Align.L)
    pdf.ln(2)

    # Sec: Dados do pedido
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Dados do Pedido", ln=1)
    pdf.set_font("helvetica", "", 10)

    order_id = _coalesce(order.get("id"))
    created_at = _fmt_dt_iso8601(order.get("created_at") or order.get("created_at_iso"))
    buyer_email = sanitize_text(_coalesce(order.get("buyer_email"), order.get("email")))
    buyer_ip = sanitize_text(_coalesce(order.get("buyer_ip_address"), order.get("buyer_ip")))
    currency = sanitize_text(_coalesce(order.get("currency"), order.get("currency_code"), "BRL"))
    settled_gross = _coalesce(order.get("settled_gross"), order.get("settled_gross_cents"))
    try:
        if isinstance(settled_gross, int):
            valor_pago = float(settled_gross) / 100.0
        else:
            valor_pago = float(str(settled_gross))
    except Exception:
        valor_pago = 0.0

    # Tabela pedido
    pdf.table_row([("ID do Pedido:", 40), (str(order_id), 120)])
    pdf.table_row([("Data da Compra:", 40), (created_at, 120)])
    pdf.table_row([("E-mail do Comprador:", 40), (buyer_email, 120)])
    pdf.table_row([("IP da Compra:", 40), (buyer_ip, 120)])
    pdf.table_row([("Moeda:", 40), (currency, 120)])
    pdf.table_row([("Valor Pago:", 40), (_format_currency_brl(valor_pago), 120)])
    pdf.ln(2)

    # Sec: Métricas de reembolso (ilustrativas)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Análise para Cancelamento/Reembolso", ln=1)
    pdf.set_font("helvetica", "", 10)

    # Tenta estimar 'arquivos totais' a partir de items do pedido (quando disponível)
    download_items = order.get("download_items") or order.get("items") or []
    total_files = len({str(item.get("file_id", item.get("id", item))) for item in download_items}) if download_items else 0
    baixados = len({str(dl.get("file_id", dl.get("id", dl.get("product_id")))) for dl in downloads}) if downloads else 0
    faltantes = max(total_files - baixados, 0)
    prop_reembolso = f"{faltantes} / {total_files}" if total_files else "0 / 0"
    valor_reembolso = (valor_pago * faltantes / total_files) if total_files else 0.0

    pdf.table_row([("Arquivos Totais:", 60), (str(total_files), 100)], bold_first=True)
    pdf.table_row([("Arquivos Baixados:", 60), (str(baixados), 100)], bold_first=True)
    pdf.table_row([("Proporção p/ Reembolso:", 60), (prop_reembolso, 100)], bold_first=True)
    pdf.table_row([("Valor Reembolso (ilustrativo):", 60), (_format_currency_brl(valor_reembolso), 100)], bold_first=True)
    pdf.ln(2)

    # Sec: Histórico de downloads
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, "Histórico de Downloads", ln=1)
    pdf.set_font("helvetica", "", 10)

    if not downloads:
        pdf.cell(0, 8, "Nenhum download registrado para este pedido.", ln=1)
    else:
        # Cabeçalho
        pdf.set_fill_color(230, 230, 230)
        pdf.set_font("helvetica", "B", 10)
        col_w = (15, 115, 50)  # #, Produto, Data
        headers = ["#", "Nome do Produto", "Data do Download"]
        for i, h in enumerate(headers):
            pdf.cell(col_w[i], 8, h, border=1, align=Align.C, fill=True)
        pdf.ln(8)

        pdf.set_font("helvetica", "", 9)
        for i, dl in enumerate(downloads, start=1):
            product_name = sanitize_text(
                _coalesce(dl.get("product_name"), dl.get("file_name"), f"Produto {dl.get('product_id','?')}")
            )
            when = _fmt_dt_iso8601(dl.get("created_at") or dl.get("downloaded_at"))
            pdf.cell(col_w[0], 7, str(i), border=1, align=Align.C)
            pdf.cell(col_w[1], 7, product_name, border=1)
            pdf.cell(col_w[2], 7, when, border=1, align=Align.C)
            pdf.ln(7)

    # === LINHA CRÍTICA: garantir BYTES, não bytearray ===
    raw = pdf.output(dest="S")  # fpdf2 retorna geralmente bytearray
    if isinstance(raw, (bytearray, bytes)):
        return bytes(raw)
    # fallback raro (libs antigas podem devolver str)
    return str(raw).encode("latin-1", "replace")


# =========================
# UI — Streamlit
# =========================
def build_ui():
    st.title("📄 Relatório de Downloads — SendOwl")

    with st.expander("🔐 Credenciais (API Key / Secret)", expanded=True):
        col1, col2 = st.columns(2)
        api_key = col1.text_input("SendOwl API Key", value=os.getenv("SENDOWL_API_KEY", ""), type="password")
        api_secret = col2.text_input("SendOwl API Secret", value=os.getenv("SENDOWL_API_SECRET", ""), type="password")

    st.markdown("---")

    col_a, col_b = st.columns([1, 2])
    order_id = col_a.text_input("ID do Pedido", placeholder="Ex.: 123456789")

    run = st.button("🔎 Buscar dados do pedido", type="primary", use_container_width=True)

    if not run:
        st.info("Informe suas credenciais e o ID do pedido, depois clique em **Buscar dados do pedido**.")
        return

    if not api_key or not api_secret:
        st.error("API Key e API Secret são obrigatórios.")
        return

    try:
        headers = _make_auth_header(api_key, api_secret)

        with st.spinner("Consultando pedido..."):
            order = fetch_order(order_id, headers)

        with st.spinner("Consultando downloads..."):
            downloads = fetch_order_downloads(order_id, headers)

        # Mostra resumo e tabela
        st.success("Dados carregados com sucesso!")
        col_l, col_r = st.columns(2)
        col_l.metric("ID do Pedido", str(_coalesce(order.get("id"))))
        created_at = _fmt_dt_iso8601(order.get("created_at") or order.get("created_at_iso"))
        col_r.metric("Data da Compra", created_at)

        # Tabela de downloads
        if downloads:
            df = pd.DataFrame([
                {
                    "Produto": _coalesce(d.get("product_name"), d.get("file_name"), f"Produto {d.get('product_id','?')}"),
                    "Data do Download": _fmt_dt_iso8601(d.get("created_at") or d.get("downloaded_at")),
                    "Product ID": _coalesce(d.get("product_id")),
                    "File ID": _coalesce(d.get("file_id")),
                }
                for d in downloads
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("Nenhum download encontrado para este pedido.")

        # Botão de PDF
        order_name_str = f"{_coalesce(order.get('id'))} — {_coalesce(order.get('buyer_email'), order.get('email'), '')}"
        try:
            pdf_bytes = generate_pdf_bytes(order, downloads, order_name_str)
            st.download_button(
                label="⬇️ Gerar PDF",
                data=pdf_bytes,  # **bytes** (NÃO bytearray)
                file_name=f"relatorio_pedido_{_coalesce(order.get('id'))}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e!s}")

    except requests.HTTPError as http_err:
        # Mensagens de API legíveis
        try:
            detail = http_err.response.json()
        except Exception:
            detail = http_err.response.text if http_err.response is not None else ""
        st.error(f"Erro HTTP ao falar com o SendOwl: {http_err!s}\nDetalhes: {detail}")
    except requests.RequestException as net_err:
        st.error(f"Erro de rede ao falar com o SendOwl: {net_err!s}")
    except Exception as e:
        st.error(f"Erro inesperado: {e!s}")


def main():
    try:
        build_ui()
    except Exception as e:
        st.error(f"Falha inesperada na aplicação: {e!s}")


if __name__ == "__main__":
    main()

