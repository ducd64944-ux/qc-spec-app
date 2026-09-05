"""
app.py — QC Thông số kỹ thuật (QC Spec App), chạy hàng loạt

App Streamlit giúp QC bài viết thông số kỹ thuật sản phẩm (TGDĐ/ĐMX) so với
spec + ảnh do hãng cung cấp. Nhập nhiều dòng cùng lúc: ID sản phẩm + link bài
viết TGDĐ/ĐMX + link trang hãng (nếu có), bấm 1 nút để chạy QC cho tất cả.

Không dùng bảng mapping ID thuộc tính (attribute_mapping.py) — đối chiếu
trực tiếp bằng fuzzy match tên thuộc tính (matcher.py). Khi không có link
hãng, chỉ liệt kê TSKT đọc được từ bài viết (không so trạng thái Khớp/Lệch).

Chạy local:  streamlit run app.py
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from image_compare import compare_image_sets
from matcher import match_specs_fuzzy
from scraper import scrape_page

STATUS_COLORS = {
    "Khớp": "#d4edda",
    "Lệch": "#f8d7da",
    "Nghi ngờ": "#fff3cd",
    "Thiếu": "#e2e3e5",
    "Không khớp": "#f8d7da",
    "Lỗi tải ảnh": "#e2e3e5",
}

EMPTY_ROW = {"ID": "", "Link bài viết": "", "Link hãng": ""}


def _style_status_column(df: pd.DataFrame, status_col: str = "Trạng thái"):
    def _row_style(row):
        color = STATUS_COLORS.get(row[status_col], "")
        return [f"background-color: {color}" for _ in row]
    return df.style.apply(_row_style, axis=1)


def _init_session_state():
    st.session_state.setdefault("batch_input", pd.DataFrame([EMPTY_ROW]))
    st.session_state.setdefault("batch_results", None)


def _process_one_product(product_id: str, url_a: str, url_b: str) -> dict:
    """Cào + đối chiếu 1 sản phẩm. Lỗi ở 1 sản phẩm không được làm hỏng cả
    lô — luôn trả về dict, lỗi được ghi vào result['error']."""
    result: dict = {"id": product_id, "url_a": url_a, "url_b": url_b, "error": None}

    try:
        page_a = scrape_page(url_a)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Lỗi tải bài viết TGDĐ/ĐMX: {exc}"
        return result
    result["page_a"] = page_a

    page_b = None
    if url_b:
        try:
            page_b = scrape_page(url_b)
        except Exception as exc:  # noqa: BLE001
            result["warning_b"] = f"Lỗi tải trang hãng: {exc}"
    result["page_b"] = page_b

    if page_b is not None:
        fuzzy_rows = match_specs_fuzzy(page_a.specs, page_b.specs)
        result["table_rows"] = [{
            "Thuộc tính (TGDĐ/ĐMX)": r.label_a or "—",
            "Giá trị (TGDĐ/ĐMX)": r.value_a,
            "Thuộc tính (Hãng)": r.label_b or "—",
            "Giá trị (Hãng)": r.value_b,
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
        # Chưa có link hãng -> chỉ liệt kê TSKT đọc được, không so trạng thái
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

    if r.get("warning_b"):
        st.warning(f"[Trang hãng] {r['warning_b']}")
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
        st.caption("Chưa có link trang hãng — chỉ liệt kê TSKT đọc được từ bài viết.")
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
                if m.url_a:
                    st.image(m.url_a, caption="TGDĐ/ĐMX", use_container_width=True)
            with cols[1]:
                if m.url_b:
                    st.image(m.url_b, caption="Hãng", use_container_width=True)
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
    st.set_page_config(page_title="QC Thông số kỹ thuật", layout="wide")
    st.title("QC Thông số kỹ thuật (TGDĐ/ĐMX) — hàng loạt")
    st.caption(
        "Dán nhiều dòng: ID sản phẩm, link bài viết TGDĐ/ĐMX (bắt buộc), "
        "link trang hãng (tuỳ chọn). Bấm \"Chạy QC hàng loạt\" để đối chiếu "
        "thông số kỹ thuật + ảnh sản phẩm cho tất cả cùng lúc."
    )

    _init_session_state()

    edited = st.data_editor(
        st.session_state["batch_input"],
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "ID": st.column_config.TextColumn("ID sản phẩm", width="small"),
            "Link bài viết": st.column_config.TextColumn(
                "Link bài viết TGDĐ/ĐMX", width="large"
            ),
            "Link hãng": st.column_config.TextColumn(
                "Link trang hãng (tuỳ chọn)", width="large"
            ),
        },
        key="batch_editor",
    )
    st.session_state["batch_input"] = edited

    if st.button("Chạy QC hàng loạt", type="primary"):
        rows = edited.fillna("").to_dict("records")
        valid_rows = [r for r in rows if str(r.get("Link bài viết", "")).strip()]

        if not valid_rows:
            st.error("Chưa có dòng nào có link bài viết TGDĐ/ĐMX.")
        else:
            results = []
            progress = st.progress(0.0)
            for i, row in enumerate(valid_rows):
                pid = str(row.get("ID", "")).strip() or f"(dòng {i + 1})"
                url_a = str(row.get("Link bài viết", "")).strip()
                url_b = str(row.get("Link hãng", "")).strip()
                with st.spinner(f"Đang xử lý {pid}..."):
                    results.append(_process_one_product(pid, url_a, url_b))
                progress.progress((i + 1) / len(valid_rows))
            st.session_state["batch_results"] = results

    _render_batch_results()


if __name__ == "__main__":
    main()
