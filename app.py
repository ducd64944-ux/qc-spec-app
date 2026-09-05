"""
app.py — QC Thông số kỹ thuật (QC Spec App)

App Streamlit giúp QC bài viết thông số kỹ thuật sản phẩm (TGDĐ/ĐMX) so với
spec + ảnh do hãng cung cấp, bằng cách dán 2 link.

Chạy local:  streamlit run app.py
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import attribute_mapping as am
import matcher
from image_compare import compare_image_sets
from scraper import scrape_page

APP_DIR = os.path.dirname(os.path.abspath(__file__))

STATUS_COLORS = {
    "Khớp": "#d4edda",
    "Lệch": "#f8d7da",
    "Nghi ngờ": "#fff3cd",
    "Thiếu": "#e2e3e5",
    "Không khớp": "#f8d7da",
    "Lỗi tải ảnh": "#e2e3e5",
}


def _style_status_column(df: pd.DataFrame, status_col: str = "Trạng thái"):
    def _row_style(row):
        color = STATUS_COLORS.get(row[status_col], "")
        return [f"background-color: {color}" for _ in row]
    return df.style.apply(_row_style, axis=1)


def _init_session_state():
    st.session_state.setdefault("mapping_result", None)
    st.session_state.setdefault("qc_result", None)


def _render_mapping_panel():
    st.subheader("Quản lý mapping thuộc tính")

    default_result = am.load_default_mapping(app_dir=APP_DIR)

    override_file = st.file_uploader(
        "Override tạm (không lưu vào repo) — upload 1 bản mapping_thuoc_tinh.xlsx khác để thử",
        type=["xlsx"],
        key="mapping_override_uploader",
    )

    if override_file is not None:
        result = am.load_mapping_from_upload(override_file)
        if result.ok:
            st.info("Đang dùng bản mapping override tạm thời (chỉ áp dụng cho lần chạy này).")
    else:
        result = default_result

    if result.ok:
        st.success(f"Đã đọc mapping: {len(result.dataframe)} dòng — nguồn: {result.source_path}")
        with st.expander("Xem trước mapping đang dùng"):
            st.dataframe(result.dataframe, use_container_width=True)
    else:
        st.warning(
            f"Chưa dùng được mapping theo ID ({result.error}). "
            "App sẽ tạm dùng fuzzy match tên thuộc tính (kém chắc chắn hơn) cho lần chạy này."
        )

    if default_result.ok:
        buffer = am.build_updated_mapping_with_suggestions(default_result.dataframe, [])
        st.download_button(
            "Tải mapping hiện tại trong repo",
            data=buffer,
            file_name="mapping_thuoc_tinh.xlsx",
            key="download_current_mapping",
        )

    st.session_state["mapping_result"] = result
    return result


def _run_qc(url_dmx: str, url_hang: str, mapping_result: am.MappingLoadResult):
    with st.spinner("Đang tải và phân tích bài viết TGDĐ/ĐMX..."):
        page_a = scrape_page(url_dmx)

    page_b = None
    if url_hang:
        with st.spinner("Đang tải và phân tích trang hãng..."):
            page_b = scrape_page(url_hang)

    for w in page_a.warnings:
        st.warning(f"[Bài viết TGDĐ/ĐMX] {w}")
    if page_b:
        for w in page_b.warnings:
            st.warning(f"[Trang hãng] {w}")

    specs_b = page_b.specs if page_b else {}

    unmapped: list = []
    if mapping_result.ok:
        id_rows, unmapped = am.match_specs_by_id(page_a.specs, specs_b, mapping_result.dataframe)
        table_rows = [{
            "ID": r.attribute_id,
            "Thuộc tính": r.ten_chuan or f"{r.label_a} / {r.label_b}",
            "Giá trị (TGDĐ/ĐMX)": r.value_a,
            "Giá trị (Hãng)": r.value_b,
            "Trạng thái": r.status,
            "Cách khớp": "ID (chính xác)" if r.match_method == "id_exact" else "ID (fuzzy trong alias)",
        } for r in id_rows]
        method_label = "ID mapping"
    else:
        fuzzy_rows = matcher.match_specs_fuzzy(page_a.specs, specs_b)
        table_rows = [{
            "ID": "",
            "Thuộc tính": f"{r.label_a or '(?)'} / {r.label_b or '(?)'}",
            "Giá trị (TGDĐ/ĐMX)": r.value_a,
            "Giá trị (Hãng)": r.value_b,
            "Trạng thái": r.status,
            "Cách khớp": "Fuzzy tên thuộc tính",
        } for r in fuzzy_rows]
        method_label = "Fuzzy (chưa có mapping ID)"

    st.session_state["qc_result"] = {
        "page_a": page_a,
        "page_b": page_b,
        "table_rows": table_rows,
        "unmapped": unmapped,
        "method_label": method_label,
    }


def _render_qc_result():
    result = st.session_state.get("qc_result")
    if not result:
        return

    st.subheader("Kết quả đối chiếu thông số")
    st.caption(f"Phương pháp đối chiếu: {result['method_label']}")

    df = pd.DataFrame(result["table_rows"])
    if df.empty:
        st.info("Không có thông số nào để đối chiếu.")
    else:
        counts = df["Trạng thái"].value_counts()
        summary_cols = st.columns(len(STATUS_COLORS))
        for i, (status, _) in enumerate(STATUS_COLORS.items()):
            with summary_cols[i]:
                st.metric(status, int(counts.get(status, 0)))
        st.dataframe(_style_status_column(df), use_container_width=True, height=480)

    unmapped = result["unmapped"]
    if unmapped:
        st.subheader("Thuộc tính chưa có trong mapping")
        st.caption(
            "Các thuộc tính này xuất hiện trong lần chạy nhưng chưa nằm trong "
            "mapping_thuoc_tinh.xlsx. Tải file gợi ý bên dưới, điền cột ID, "
            "rồi merge/commit đè vào repo."
        )
        unmapped_df = pd.DataFrame([{"Nguồn": u.side, "Nhãn": u.label, "Giá trị": u.value} for u in unmapped])
        st.dataframe(unmapped_df, use_container_width=True)

        mapping_result = st.session_state.get("mapping_result")
        if mapping_result and mapping_result.ok:
            buffer = am.build_updated_mapping_with_suggestions(mapping_result.dataframe, unmapped)
            st.download_button(
                "Tải mapping đã gộp kèm gợi ý dòng mới",
                data=buffer,
                file_name="mapping_thuoc_tinh_suggestions.xlsx",
                key="download_suggested_mapping",
            )

    page_b = result["page_b"]
    if page_b is not None:
        st.subheader("Đối chiếu ảnh sản phẩm")
        images_a = result["page_a"].images
        images_b = page_b.images
        if not images_a or not images_b:
            st.info("Thiếu ảnh ở 1 trong 2 nguồn nên chưa thể so sánh ảnh.")
        else:
            with st.spinner("Đang tải ảnh và so sánh (perceptual hash)..."):
                image_matches = compare_image_sets(images_a, images_b)
            for match in image_matches:
                cols = st.columns([1, 1, 1])
                with cols[0]:
                    if match.url_a:
                        st.image(match.url_a, caption="TGDĐ/ĐMX", use_container_width=True)
                with cols[1]:
                    if match.url_b:
                        st.image(match.url_b, caption="Hãng", use_container_width=True)
                with cols[2]:
                    color = STATUS_COLORS.get(match.status, "")
                    st.markdown(
                        f"<div style='background-color:{color};padding:8px;border-radius:6px'>"
                        f"<b>{match.status}</b><br/>"
                        f"Khoảng cách phash: {match.distance if match.distance is not None else '—'}"
                        f"{'<br/>' + match.error if match.error else ''}"
                        "</div>",
                        unsafe_allow_html=True,
                    )
                st.divider()
    else:
        st.caption("Chưa dán link trang hãng nên bỏ qua bước đối chiếu ảnh.")


def main():
    st.set_page_config(page_title="QC Thông số kỹ thuật", layout="wide")
    st.title("QC Thông số kỹ thuật (TGDĐ/ĐMX)")
    st.caption(
        "Dán link bài viết trên web TGDĐ/ĐMX và (nếu có) link trang hãng để "
        "đối chiếu thông số kỹ thuật + ảnh sản phẩm."
    )

    _init_session_state()

    with st.form("qc_form"):
        url_dmx = st.text_input("Link bài viết TGDĐ/ĐMX (bắt buộc)", placeholder="https://www.dienmayxanh.com/...")
        url_hang = st.text_input("Link trang hãng (tuỳ chọn)", placeholder="https://www.samsung.com/...")
        submitted = st.form_submit_button("Chạy QC", type="primary")

    mapping_result = _render_mapping_panel()

    if submitted:
        if not url_dmx.strip():
            st.error("Vui lòng dán link bài viết TGDĐ/ĐMX.")
        else:
            try:
                _run_qc(url_dmx.strip(), url_hang.strip(), mapping_result)
            except Exception as exc:  # noqa: BLE001 - báo lỗi rõ ràng cho người dùng thay vì crash app
                st.error(f"Lỗi khi chạy QC: {exc}")

    _render_qc_result()


if __name__ == "__main__":
    main()
