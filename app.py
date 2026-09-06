"""
app.py — QC Thông số kỹ thuật (QC Spec App), chạy hàng loạt

App Streamlit giúp QC bài viết thông số kỹ thuật sản phẩm (TGDĐ/ĐMX) so với
spec + ảnh do hãng cung cấp. Nhập nhiều dòng cùng lúc: ID sản phẩm + link bài
viết TGDĐ/ĐMX + link trang hãng (nếu có), bấm 1 nút để chạy QC cho tất cả.

"Nguồn đối chiếu" (spec + ảnh để so sánh) cho mỗi sản phẩm có thể là MỘT
TRONG HAI cách (không bắt buộc cả hai):
  - Link trang hãng (cột "Link hãng") -> tự động cào như bài viết TGDĐ/ĐMX.
  - File tự upload (ảnh/pdf/xlsx/csv/txt) -> dùng khi sản phẩm không có link
    hãng rõ ràng để cào. Đặt tên file bắt đầu bằng (hoặc chứa) ID sản phẩm để
    hệ thống tự ghép đúng file vào đúng dòng.
Nếu 1 dòng có cả link hãng lẫn file upload khớp ID, ưu tiên dùng link hãng.

Không dùng bảng mapping ID thuộc tính (attribute_mapping.py) — đối chiếu
trực tiếp bằng fuzzy match tên thuộc tính (matcher.py). Khi không có nguồn
đối chiếu nào, chỉ liệt kê TSKT đọc được từ bài viết (không so trạng thái
Khớp/Lệch).

Chạy local:  streamlit run app.py
"""

from __future__ import annotations

import io
import json
import os
import re

import pandas as pd
import streamlit as st

from image_compare import compare_image_sets
from local_source import load_local_source
from matcher import match_specs_fuzzy
from scraper import SAVED_COOKIE_FILE, resolve_product_id_url, scrape_page

# Nhận ID sản phẩm trần (vd "370907" -> mặc định ĐMX) hoặc có tiền tố domain
# (vd "tgdd:123456" -> TGDĐ). Chỉ áp dụng cho cột "Link bài viết" khi giá trị
# không phải là 1 URL http(s) đầy đủ.
_PRODUCT_ID_PATTERN = re.compile(r"^(?:(dmx|tgdd):)?(\d{4,})$", re.IGNORECASE)

# Domain của link rút gọn /sp-<id>, dùng để tạo link "mở nhanh" cho Đức tự
# bấm bằng trình duyệt thật (có đủ cookie/JS) khi app không tự resolve được.
_SHORTCUT_DOMAINS = {"dmx": "www.dienmayxanh.com", "tgdd": "www.thegioididong.com"}

# Bảng màu tonal theo palette Material You / seed xanh lá ĐMX bên dưới
# (_THEME_CSS), để trạng thái Khớp/Lệch/... đồng bộ với theme chung thay vì
# màu Bootstrap mặc định cũ.
STATUS_COLORS = {
    "Khớp": "#DCF3E0",       # secondary container (xanh lá nhạt)
    "Lệch": "#FBE3E0",       # đỏ tonal
    "Nghi ngờ": "#FCEFC7",   # vàng/gold tonal (tertiary)
    "Thiếu": "#E9ECE6",      # xám tonal trung tính
    "Không khớp": "#FBE3E0",
    "Lỗi tải ảnh": "#E9ECE6",
}

# --- Theme "Material You" (seed xanh lá + vàng ĐMX) --------------------------
# Màu cơ bản (primaryColor, backgroundColor...) đã đặt trong
# .streamlit/config.toml — đây là cách idiomatic của Streamlit, áp dụng cho
# mọi widget gốc mà không cần hack CSS. Khối CSS dưới đây chỉ bổ sung các
# token Material You mà config.toml không hỗ trợ: bo góc lớn (24px), nút
# pill-shape, font Roboto, hiệu ứng hover/focus, shadow tonal — áp cho các
# phần tử Streamlit có thể target bằng CSS an toàn (button, alert, expander,
# input, container ảnh/kết quả). Bảng nhập liệu (st.data_editor) và bảng kết
# quả (st.dataframe) dùng canvas nội bộ (glide-data-grid) nên không thể theme
# sâu bằng CSS — chỉ bo góc/đổ bóng được phần khung bọc ngoài.
_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Roboto', 'Segoe UI', sans-serif !important;
}

:root {
    --md-primary: #1B8A3D;
    --md-on-primary: #FFFFFF;
    --md-secondary-container: #DCF3E0;
    --md-on-secondary-container: #0D3B1E;
    --md-tertiary: #C99A00;
    --md-on-tertiary: #3A2900;
    --md-surface: #FBFDF7;
    --md-surface-container: #EEF3EA;
    --md-surface-container-low: #E2E8DB;
    --md-outline: #74796D;
}

/* Nút bấm: pill-shape + state layer + tactile feedback */
.stButton > button, .stDownloadButton > button {
    border-radius: 999px !important;
    border: none !important;
    padding: 0.5rem 1.5rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.01em;
    transition: all 200ms cubic-bezier(0.2, 0, 0, 1) !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"], .stDownloadButton > button {
    background-color: var(--md-primary) !important;
    color: var(--md-on-primary) !important;
}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button:hover {
    background-color: #16702F !important;
    box-shadow: 0 2px 6px rgba(27, 138, 61, 0.35) !important;
    transform: translateY(-1px);
}
.stButton > button[kind="primary"]:active, .stDownloadButton > button:active {
    transform: scale(0.97);
}
.stButton > button[kind="secondary"] {
    background-color: var(--md-secondary-container) !important;
    color: var(--md-on-secondary-container) !important;
}
.stButton > button[kind="secondary"]:hover {
    filter: brightness(0.96);
}

/* Card/container bo góc lớn kiểu Material You (expander, alert, khung ảnh) */
div[data-testid="stExpander"] {
    border-radius: 20px !important;
    border: 1px solid var(--md-outline) !important;
    background-color: var(--md-surface-container) !important;
    overflow: hidden;
}
div[data-testid="stExpander"] summary {
    font-weight: 500 !important;
}

div[data-testid="stAlert"] {
    border-radius: 16px !important;
}

/* Input/textarea: bo góc, viền focus theo màu primary */
.stTextInput input, .stTextArea textarea {
    border-radius: 12px !important;
    transition: border-color 200ms ease !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--md-primary) !important;
    box-shadow: 0 0 0 1px var(--md-primary) !important;
}

/* Metric (đếm Khớp/Lệch/...): thành chip tonal thay vì số trần */
div[data-testid="stMetric"] {
    background-color: var(--md-surface-container-low);
    border-radius: 16px;
    padding: 0.75rem 0.5rem;
    text-align: center;
}

/* File uploader: bo góc lớn đồng bộ */
div[data-testid="stFileUploaderDropzone"] {
    border-radius: 20px !important;
    background-color: var(--md-surface-container) !important;
}

/* Khung bọc bảng nhập liệu / bảng kết quả: bo góc + đổ bóng nhẹ */
div[data-testid="stDataFrame"], div[data-testid="stDataFrameResizable"] {
    border-radius: 16px !important;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(26, 28, 24, 0.12);
}

/* Tiêu đề trang */
h1 {
    font-weight: 700 !important;
    letter-spacing: -0.01em;
    color: #10371D;
}
h2, h3 {
    font-weight: 500 !important;
}

/* Vùng nội dung chính: thêm khoảng đệm rộng rãi hơn kiểu Material You */
.block-container {
    padding-top: 2rem !important;
    padding-bottom: 3rem !important;
}
</style>
"""


def _inject_theme_css():
    st.markdown(_THEME_CSS, unsafe_allow_html=True)

EMPTY_ROW = {"ID": "", "Link bài viết": "", "Link hãng": ""}


def _style_status_column(df: pd.DataFrame, status_col: str = "Trạng thái"):
    def _row_style(row):
        color = STATUS_COLORS.get(row[status_col], "")
        return [f"background-color: {color}" for _ in row]
    return df.style.apply(_row_style, axis=1)


def _init_session_state():
    st.session_state.setdefault("batch_input", pd.DataFrame([EMPTY_ROW]))
    st.session_state.setdefault("batch_results", None)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _match_files_for_id(files: list, product_id: str) -> list:
    """Ghép file upload vào đúng sản phẩm dựa trên tên file có chứa ID (sau
    khi bỏ ký tự không phải chữ/số để không phụ thuộc dấu -, _, khoảng trắng...)."""
    pid_norm = _normalize_for_match(product_id)
    if not pid_norm or not files:
        return []
    matched = []
    for f in files:
        name_norm = _normalize_for_match(getattr(f, "name", ""))
        if pid_norm in name_norm:
            matched.append(f)
    return matched


def _resolve_article_url(raw: str) -> str:
    """Nếu raw là URL http(s) đầy đủ thì giữ nguyên. Nếu raw là 1 ID sản
    phẩm trần (vd "370907", mặc định ĐMX) hoặc có tiền tố domain (vd
    "tgdd:123456"), tự resolve sang URL bài viết đầy đủ qua link rút gọn
    /sp-<id>. Không dùng cookie đăng nhập/token cá nhân nào — chỉ "làm
    nóng" bằng 1 lượt GET trang chủ (xem resolve_product_id_url)."""
    raw = raw.strip()
    if raw.lower().startswith("http"):
        return raw
    match = _PRODUCT_ID_PATTERN.match(raw)
    if match:
        domain_key = (match.group(1) or "dmx").lower()
        product_id = match.group(2)
        return resolve_product_id_url(product_id, domain_key)
    return raw  # để nguyên -> scrape_page sẽ báo lỗi rõ ràng khi không tải được


def _shortcut_link_for_id(raw: str) -> str | None:
    """Nếu raw là 1 ID sản phẩm trần (không phải URL đầy đủ), trả về link rút
    gọn /sp-<id> để Đức tự bấm mở bằng trình duyệt thật (có đủ cookie/JS mà
    server của app không có) — chỉ tạo link, không tự tải/fetch gì cả."""
    raw = raw.strip()
    if raw.lower().startswith("http"):
        return None
    match = _PRODUCT_ID_PATTERN.match(raw)
    if not match:
        return None
    domain_key = (match.group(1) or "dmx").lower()
    product_id = match.group(2)
    domain = _SHORTCUT_DOMAINS.get(domain_key, _SHORTCUT_DOMAINS["dmx"])
    return f"https://{domain}/sp-{product_id}"


def _render_cookie_manager():
    """Khung quản lý JSON Cookie (định dạng xuất từ EditThisCookie) dùng để
    resolve link /sp-<id> khi cách "làm nóng" mặc định bị chặn 404. Đức tự
    dán cookie từ trình duyệt của mình vào đây — đây là dữ liệu do Đức tự
    cung cấp và tự chịu trách nhiệm, không phải app tự thu thập. Cookie được
    lưu vào SAVED_COOKIE_FILE cục bộ (không commit lên git — xem .gitignore).

    Lưu ý bảo mật: nếu app này được deploy công khai (ai cũng xem được, như
    Streamlit Community Cloud mặc định), NỘI DUNG cookie đã lưu sẽ hiển thị
    lại trong ô text_area cho BẤT KỲ AI mở app — kể cả token đăng nhập thật.
    Nên hạn chế người xem app (Streamlit Cloud > Settings > Sharing) nếu có
    dán cookie thật vào đây."""
    with st.expander("⚙️ Quản lý JSON Cookie (Vượt rào 404)", expanded=False):
        st.caption(
            "Dán JSON cookie xuất từ tiện ích EditThisCookie (khi đang đăng "
            "nhập/mở trang dienmayxanh.com hoặc thegioididong.com bằng chính "
            "trình duyệt của Đức) để giúp resolve link /sp-<id> đáng tin cậy "
            "hơn. ⚠️ Cookie này là dữ liệu phiên cá nhân — nếu app được deploy "
            "công khai, bất kỳ ai mở app cũng thấy được nội dung đã lưu ở "
            "đây. Nên giới hạn người xem app nếu dán cookie thật."
        )

        try:
            with open(SAVED_COOKIE_FILE, "r", encoding="utf-8") as f:
                default_text = f.read()
        except (OSError, FileNotFoundError):
            default_text = ""

        cookie_text = st.text_area(
            "JSON Cookie", value=default_text, height=160,
            key="cookie_json_input",
            placeholder='[{"domain": ".dienmayxanh.com", "name": "...", "value": "...", ...}, ...]',
        )

        if st.button("Lưu Cookie", key="save_cookie_btn"):
            stripped = cookie_text.strip()
            if not stripped:
                try:
                    os.remove(SAVED_COOKIE_FILE)
                except FileNotFoundError:
                    pass
                st.success("Đã xoá cookie đã lưu (ô trống).")
            else:
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    st.error(f"JSON không hợp lệ: {exc}")
                else:
                    if not isinstance(parsed, list):
                        st.error(
                            "JSON hợp lệ nhưng không phải dạng danh sách "
                            "(list) cookie — dán đúng định dạng xuất từ "
                            "EditThisCookie (mảng [...] các object cookie)."
                        )
                    else:
                        with open(SAVED_COOKIE_FILE, "w", encoding="utf-8") as f:
                            f.write(stripped)
                        st.success(f"Đã lưu {len(parsed)} cookie vào {SAVED_COOKIE_FILE}.")


def _image_display(ref):
    """ref có thể là URL (str) hoặc tuple (nhãn, bytes) -> trả về dạng
    st.image chấp nhận được."""
    if isinstance(ref, tuple):
        return ref[1]
    return ref


def _process_one_product(product_id: str, url_a: str, url_b: str,
                          local_files_b: list | None = None) -> dict:
    """Cào + đối chiếu 1 sản phẩm. Lỗi ở 1 sản phẩm không được làm hỏng cả
    lô — luôn trả về dict, lỗi được ghi vào result['error'].

    Nguồn đối chiếu (page_b) ưu tiên theo thứ tự: link hãng (url_b) trước,
    nếu không có mới dùng file upload (local_files_b)."""
    result: dict = {"id": product_id, "url_a": url_a, "url_b": url_b, "error": None}

    try:
        resolved_url_a = _resolve_article_url(url_a)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Lỗi phân giải ID sản phẩm '{url_a}': {exc}"
        return result

    try:
        page_a = scrape_page(resolved_url_a)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Lỗi tải bài viết TGDĐ/ĐMX: {exc}"
        return result
    result["page_a"] = page_a
    result["url_a"] = resolved_url_a

    page_b = None
    source_b_label = ""
    if url_b:
        try:
            page_b = scrape_page(url_b)
            source_b_label = "link trang hãng"
        except Exception as exc:  # noqa: BLE001
            result["warning_b"] = f"Lỗi tải trang hãng: {exc}"
    elif local_files_b:
        page_b = load_local_source(local_files_b)
        source_b_label = "file đã upload (" + ", ".join(
            getattr(f, "name", "?") for f in local_files_b
        ) + ")"
        if page_b.warnings:
            result["warning_b"] = "; ".join(page_b.warnings)

    result["page_b"] = page_b
    result["source_b_label"] = source_b_label

    if page_b is not None:
        fuzzy_rows = match_specs_fuzzy(page_a.specs, page_b.specs)
        result["table_rows"] = [{
            "Thuộc tính (TGDĐ/ĐMX)": r.label_a or "—",
            "Giá trị (TGDĐ/ĐMX)": r.value_a,
            "Thuộc tính (nguồn đối chiếu)": r.label_b or "—",
            "Giá trị (nguồn đối chiếu)": r.value_b,
            "Trạng thái": r.status,
        } for r in fuzzy_rows]
        result["has_comparison"] = True

        images_a, images_b = page_a.images, page_b.images
        if images_a and images_b:
            try:
                result["image_matches"] = compare_image_sets(images_a, images_b)
            except Exception as exc:  # noqa: BLE001
                result["image_error"] = str(exc)
    else:
        # Chưa có nguồn đối chiếu nào -> chỉ liệt kê TSKT đọc được, không so
        # trạng thái
        result["table_rows"] = [{
            "Thuộc tính": label, "Giá trị": value,
        } for label, value in page_a.specs.items()]
        result["has_comparison"] = False

    return result


def _build_export_workbook(results: list) -> bytes | None:
    all_rows = []
    for r in results:
        if r.get("error"):
            continue
        for row in r["table_rows"]:
            all_rows.append({"ID": r["id"], **row})
    if not all_rows:
        return None

    combined = pd.DataFrame(all_rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        combined.to_excel(writer, index=False, sheet_name="QC")
    buffer.seek(0)
    return buffer.read()


def _render_product_result(r: dict):
    st.subheader(f"Sản phẩm: {r['id']}")

    if r.get("error"):
        st.error(r["error"])
        return

    if r.get("source_b_label"):
        st.caption(f"Nguồn đối chiếu: {r['source_b_label']}")

    if r.get("warning_b"):
        st.warning(f"[Nguồn đối chiếu] {r['warning_b']}")
    for w in r["page_a"].warnings:
        st.warning(f"[Bài viết TGDĐ/ĐMX] {w}")

    df = pd.DataFrame(r["table_rows"])
    if df.empty:
        st.info("Không có thông số nào để hiển thị.")
    elif r.get("has_comparison"):
        counts = df["Trạng thái"].value_counts()
        cols = st.columns(len(STATUS_COLORS))
        for i, status in enumerate(STATUS_COLORS):
            with cols[i]:
                st.metric(status, int(counts.get(status, 0)))
        st.dataframe(_style_status_column(df), use_container_width=True, height=400)
    else:
        st.caption("Chưa có nguồn đối chiếu (link hãng hoặc file upload) — chỉ liệt kê TSKT đọc được từ bài viết.")
        st.dataframe(df, use_container_width=True, height=400)

    page_b = r.get("page_b")
    if page_b is None:
        return

    images_a, images_b = r["page_a"].images, page_b.images
    if not images_a or not images_b:
        st.caption("Thiếu ảnh ở 1 trong 2 nguồn nên bỏ qua so ảnh.")
        return

    image_matches = r.get("image_matches")
    if image_matches is None:
        if r.get("image_error"):
            st.info(f"Lỗi so ảnh: {r['image_error']}")
        return

    with st.expander(f"Đối chiếu ảnh sản phẩm — {r['id']}", expanded=False):
        for m in image_matches:
            cols = st.columns([1, 1, 1])
            with cols[0]:
                if m.ref_a is not None:
                    st.image(_image_display(m.ref_a), caption=m.label_a or "TGDĐ/ĐMX",
                              use_container_width=True)
            with cols[1]:
                if m.ref_b is not None:
                    st.image(_image_display(m.ref_b), caption=m.label_b or "Nguồn đối chiếu",
                              use_container_width=True)
            with cols[2]:
                color = STATUS_COLORS.get(m.status, "")
                st.markdown(
                    f"<div style='background-color:{color};padding:8px;border-radius:6px'>"
                    f"<b>{m.status}</b><br/>"
                    f"Khoảng cách phash: {m.distance if m.distance is not None else '—'}"
                    f"{'<br/>' + m.error if m.error else ''}"
                    "</div>",
                    unsafe_allow_html=True,
                )
            st.divider()


def _render_batch_results():
    results = st.session_state.get("batch_results")
    if not results:
        return

    st.divider()
    st.header("Kết quả QC hàng loạt")

    workbook = _build_export_workbook(results)
    if workbook:
        st.download_button(
            "Tải toàn bộ kết quả (xlsx)",
            data=workbook,
            file_name="qc_ket_qua.xlsx",
            key="download_batch_results",
        )

    for r in results:
        st.divider()
        _render_product_result(r)


def main():
    st.set_page_config(page_title="QC Thông số kỹ thuật", layout="wide", page_icon="🟢")
    _inject_theme_css()
    st.title("QC Thông số kỹ thuật (TGDĐ/ĐMX) — hàng loạt")
    st.caption(
        "Dán nhiều dòng: chỉ cần ID sản phẩm (cột ID) + link trang hãng "
        "(cột Link hãng). Cột \"Link bài viết\" có thể để trống — hệ thống "
        "tự suy ra link bài viết TGDĐ/ĐMX từ ID; nếu suy ra bị lỗi (một số "
        "ID không tự resolve được), dán link bài viết đầy đủ vào cột đó để "
        "chạy chắc chắn. Bấm \"Chạy QC hàng loạt\" để đối chiếu thông số kỹ "
        "thuật + ảnh sản phẩm cho tất cả cùng lúc."
    )

    _init_session_state()

    edited = st.data_editor(
        st.session_state["batch_input"],
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "ID": st.column_config.TextColumn(
                "ID sản phẩm (vd 370907 = ĐMX, tgdd:123456 = TGDĐ)",
                width="small",
            ),
            "Link bài viết": st.column_config.TextColumn(
                "Link bài viết TGDĐ/ĐMX (để trống = tự suy ra từ ID; dán "
                "link nếu tự suy ra bị lỗi)",
                width="large",
            ),
            "Link hãng": st.column_config.TextColumn(
                "Link trang hãng / link spec (tuỳ chọn)", width="large"
            ),
        },
        key="batch_editor",
    )
    st.session_state["batch_input"] = edited

    rows_now = edited.fillna("").to_dict("records")
    quick_links = []
    for row in rows_now:
        pid_raw = str(row.get("ID", "")).strip()
        link_filled = str(row.get("Link bài viết", "")).strip()
        if pid_raw and not link_filled:
            shortcut = _shortcut_link_for_id(pid_raw)
            if shortcut:
                quick_links.append((pid_raw, shortcut))

    if quick_links:
        with st.expander(
            f"🔗 Mở nhanh trang sản phẩm theo ID ({len(quick_links)} dòng) "
            "— dùng khi tự suy ra link bị lỗi",
            expanded=False,
        ):
            st.caption(
                "App không tự tải được các link này (trang chặn request "
                "không phải từ trình duyệt thật). Bấm từng link dưới đây để "
                "mở bằng chính trình duyệt của Đức — trang sẽ tự chuyển "
                "hướng đúng bài viết — rồi copy link ở thanh địa chỉ, dán "
                "lại vào cột \"Link bài viết\" ở bảng trên."
            )
            for pid_raw, shortcut in quick_links:
                st.markdown(f"- ID `{pid_raw}`: [{shortcut}]({shortcut})")

    st.markdown("**Nguồn đối chiếu thay thế (khi sản phẩm không có link hãng)**")
    st.caption(
        "Upload ảnh/pdf/xlsx/csv/txt làm nguồn đối chiếu cho các sản phẩm "
        "không có link hãng. Đặt tên file có chứa ID sản phẩm (cột ID ở "
        "bảng trên) để hệ thống tự ghép đúng file vào đúng dòng — ví dụ ID "
        "là \"SP001\" thì đặt tên file \"SP001_spec.pdf\" hoặc \"SP001-anh1.jpg\". "
        "1 sản phẩm có thể có nhiều file (vd vừa ảnh vừa pdf)."
    )
    uploaded_files = st.file_uploader(
        "File nguồn đối chiếu",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png", "webp", "gif", "bmp", "pdf", "xlsx", "xls", "csv", "txt"],
        key="local_source_files",
    )

    _render_cookie_manager()

    if st.button("Chạy QC hàng loạt", type="primary"):
        rows = edited.fillna("").to_dict("records")
        # "Link bài viết" trống nhưng có ID -> dùng ID làm nguồn suy ra link
        # (xem _resolve_article_url), nên 1 dòng chỉ cần 1 trong 2: ID hoặc
        # link bài viết đầy đủ.
        valid_rows = [
            r for r in rows
            if str(r.get("Link bài viết", "")).strip() or str(r.get("ID", "")).strip()
        ]

        if not valid_rows:
            st.error("Chưa có dòng nào có ID hoặc link bài viết TGDĐ/ĐMX.")
        else:
            results = []
            progress = st.progress(0.0)
            for i, row in enumerate(valid_rows):
                pid = str(row.get("ID", "")).strip() or f"(dòng {i + 1})"
                url_a = str(row.get("Link bài viết", "")).strip() or str(row.get("ID", "")).strip()
                url_b = str(row.get("Link hãng", "")).strip()
                local_files_b = [] if url_b else _match_files_for_id(uploaded_files or [], pid)
                with st.spinner(f"Đang xử lý {pid}..."):
                    results.append(_process_one_product(pid, url_a, url_b, local_files_b))
                progress.progress((i + 1) / len(valid_rows))
            st.session_state["batch_results"] = results

    _render_batch_results()


if __name__ == "__main__":
    main()
