"""
sheet_source.py
Đọc "nguồn đối chiếu" từ 1 Google Sheet công khai (chia sẻ dạng "Anyone with
the link can view") theo template PIM nội bộ của Đức: có 2 cột "Tên thuộc
tính" và "Giá trị chốt" (tìm theo TÊN cột trong dòng header, không phụ thuộc
vị trí/thứ tự cột, để chịu được sheet có thêm/bớt cột khác như "Mã thuộc
tính", "Ghi chú"...).

Dùng khi 1 sản phẩm đã có sẵn sheet tổng hợp thông số "chốt" (đã duyệt nội
bộ) làm nguồn đối chiếu, thay vì link trang hãng hoặc file tự upload — ví dụ
điển hình: sản phẩm không có trang hãng chi tiết, hoặc TSKT do đội ngũ tự
tổng hợp/chuẩn hoá từ nhiều nguồn khác nhau vào 1 sheet.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

REQUEST_TIMEOUT = 20

# Tên cột cần tìm trong dòng header (không phân biệt hoa/thường, khoảng
# trắng thừa) — có thể thêm biến thể tên cột khác vào đây nếu template đổi.
LABEL_COLUMN_NAMES = ["Tên thuộc tính"]
VALUE_COLUMN_NAMES = ["Giá trị chốt"]

_SHEET_URL_PATTERN = re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)")


@dataclass
class SheetSource:
    specs: dict = field(default_factory=dict)
    images: list = field(default_factory=list)  # Google Sheet không có ảnh
    warnings: list = field(default_factory=list)


def is_google_sheet_url(url: str) -> bool:
    return bool(_SHEET_URL_PATTERN.search(url or ""))


def _extract_sheet_id_and_gid(url: str):
    match = _SHEET_URL_PATTERN.search(url)
    if not match:
        raise ValueError(f"Không phải link Google Sheets hợp lệ: {url}")
    sheet_id = match.group(1)
    gid = None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "gid" in query:
        gid = query["gid"][0]
    elif parsed.fragment:
        # vd "...#gid=806422842" -> fragment = "gid=806422842"
        frag_query = parse_qs(parsed.fragment)
        if "gid" in frag_query:
            gid = frag_query["gid"][0]
    return sheet_id, gid


def _find_column_index(header: list, candidate_names: list):
    normalized_header = [h.strip().lower() for h in header]
    for name in candidate_names:
        name_norm = name.strip().lower()
        if name_norm in normalized_header:
            return normalized_header.index(name_norm)
    return None


def load_google_sheet_source(url: str) -> SheetSource:
    """Tải 1 Google Sheet công khai dạng CSV (qua endpoint export chính thức
    của Google Sheets, không cần đăng nhập nếu sheet đã chia sẻ "Anyone with
    the link can view") và trích specs theo cột "Tên thuộc tính" / "Giá trị
    chốt". Bỏ qua các dòng có "Giá trị chốt" trống (thuộc tính chưa chốt giá
    trị — không nên đưa vào so sánh, tránh báo "Thiếu" giả)."""
    source = SheetSource()
    sheet_id, gid = _extract_sheet_id_and_gid(url)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        export_url += f"&gid={gid}"

    try:
        resp = requests.get(export_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Không tải được Google Sheet (kiểm tra sheet đã chia sẻ "
            f"\"Anyone with the link can view\" chưa): {exc}"
        ) from exc

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type:
        raise RuntimeError(
            "Google Sheet có vẻ chưa công khai — app nhận về trang đăng "
            "nhập/HTML thay vì dữ liệu CSV. Vào Chia sẻ > Anyone with the "
            "link > Viewer rồi thử lại."
        )

    text = resp.content.decode("utf-8-sig", errors="ignore")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("Google Sheet trống hoặc không đọc được dữ liệu.")

    header = rows[0]
    label_idx = _find_column_index(header, LABEL_COLUMN_NAMES)
    value_idx = _find_column_index(header, VALUE_COLUMN_NAMES)
    if label_idx is None or value_idx is None:
        raise RuntimeError(
            "Không tìm thấy cột \"Tên thuộc tính\" và/hoặc \"Giá trị chốt\" "
            "trong dòng đầu của sheet — kiểm tra lại đúng sheet/tab (gid) "
            "chứa template thông số."
        )

    for row in rows[1:]:
        if len(row) <= max(label_idx, value_idx):
            continue
        label = row[label_idx].strip()
        value = row[value_idx].strip()
        if not label or not value:
            continue  # thuộc tính chưa chốt giá trị -> bỏ qua, không so sánh
        source.specs.setdefault(label, value)

    if not source.specs:
        source.warnings.append(
            "Không có thuộc tính nào có \"Giá trị chốt\" (đã điền) trong sheet."
        )

    return source
