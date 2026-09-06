"""
sheet_source.py
Đọc "nguồn đối chiếu" từ 1 Google Sheet công khai (chia sẻ dạng "Anyone with
the link can view"), hỗ trợ NHIỀU DẠNG template PIM nội bộ:

  1. Template "Giá trị chốt": có 2 cột "Tên thuộc tính" và "Giá trị chốt"
     (tìm theo TÊN cột trong dòng header).
  2. Template "Bảng TSKT đầy đủ" (multi-product): cột A = tên thuộc tính,
     cột B = flag bắt buộc (TRUE/FALSE), cột C/D/... = giá trị từng sản phẩm.
     Tự động chọn cột sản phẩm đầu tiên có dữ liệu, HOẶC khớp theo mã/tên
     sản phẩm nếu product_hint được cung cấp.
  3. Fallback chung: cột đầu tiên = tên thuộc tính, cột cuối cùng có dữ liệu
     = giá trị — chạy khi không nhận diện được template cụ thể.

Hỗ trợ cả Google Sheets gốc lẫn file .xlsx/.xls upload lên Google Drive
(cùng endpoint /export?format=csv, Google tự chuyển đổi).

Dùng khi 1 sản phẩm đã có sẵn sheet tổng hợp thông số "chốt" (đã duyệt nội
bộ) làm nguồn đối chiếu.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

REQUEST_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Tên cột ứng viên cho mỗi template (không phân biệt hoa/thường, khoảng
# trắng thừa). Thêm biến thể vào đây khi template PIM thay đổi.
# ---------------------------------------------------------------------------

# Template 1: "Giá trị chốt"
LABEL_COLUMN_NAMES = [
    "tên thuộc tính",
    "thuộc tính",
    "tên thông số",
    "thông số",
    "tên spec",
    "spec name",
]
VALUE_COLUMN_NAMES = [
    "giá trị chốt",
    "giá trị chọn",
    "giá trị",
    "value",
    "giá trị chuẩn",
]

# Template 2: "Bảng TSKT đầy đủ" (multi-product PIM)
PIM_LABEL_COLUMN_NAMES = [
    "bảng tskt đầy đủ",
    "bảng tskt",
    "bảng thông số",
    "thông số kỹ thuật",
]

# Các dòng header/section nên bỏ qua (không phải thuộc tính thật) — xuất
# hiện trong template PIM multi-product, ví dụ "Thông tin chung",
# "Tiện ích", "Mức tiêu thụ điện năng"...
_SECTION_HEADER_KEYWORDS = {
    "thông tin chung", "mức tiêu thụ điện năng", "khả năng lọc không khí",
    "công nghệ làm lạnh", "tiện ích", "thông số kích thước/lắp đặt",
    "kèm theo máy có", "hình ảnh", "điểm nhấn bán hàng",
}

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
        frag_query = parse_qs(parsed.fragment)
        if "gid" in frag_query:
            gid = frag_query["gid"][0]
    return sheet_id, gid


def _find_column_index(header: list, candidate_names: list):
    """Tìm cột theo tên, so khớp chứa (contains) thay vì chỉ exact match,
    để chịu được header dài kiểu 'Bảng TSKT đầy đủ' hoặc
    'Thông tin bắt buộc điền\\n(Thông tin không có fill...)'."""
    normalized_header = [h.strip().lower() for h in header]
    # Thử exact match trước
    for name in candidate_names:
        name_norm = name.strip().lower()
        if name_norm in normalized_header:
            return normalized_header.index(name_norm)
    # Thử contains match (header chứa keyword)
    for name in candidate_names:
        name_norm = name.strip().lower()
        for idx, h in enumerate(normalized_header):
            if name_norm in h:
                return idx
    return None


def _is_boolean_column(rows: list, col_idx: int) -> bool:
    """Kiểm tra 1 cột có phải toàn TRUE/FALSE/rỗng không (cột flag bắt buộc
    trong template PIM multi-product)."""
    bool_values = {"true", "false", ""}
    count = 0
    matches = 0
    for row in rows[1:]:
        if col_idx >= len(row):
            continue
        count += 1
        if row[col_idx].strip().lower() in bool_values:
            matches += 1
        if count >= 15:
            break
    return count > 0 and matches / count >= 0.8


def _is_section_header(label: str) -> bool:
    """Kiểm tra label có phải là heading phân nhóm (không phải thuộc tính
    thật) — ví dụ 'Thông tin chung', 'Tiện ích'..."""
    label_lower = label.lower().strip()
    if label_lower in _SECTION_HEADER_KEYWORDS:
        return True
    # Dòng chỉ có label mà không có value thường là section header -> xử lý
    # ở caller (bỏ qua khi value rỗng)
    return False


def _pick_product_column(rows: list, data_col_indices: list,
                         product_hint: str | None = None) -> int:
    """Chọn cột sản phẩm phù hợp nhất trong sheet multi-product.

    Nếu product_hint (ID hoặc tên sản phẩm) được cung cấp, tìm cột có chứa
    product_hint trong dòng đầu tiên vài dòng (thường là dòng "Mã sản phẩm"
    hoặc "Tên sản phẩm"). Nếu không khớp, trả về cột đầu tiên."""
    if not data_col_indices:
        return 2  # fallback

    if product_hint:
        hint_lower = product_hint.strip().lower()
        # Tìm trong 5 dòng dữ liệu đầu (bỏ header), thường chứa "Mã sản
        # phẩm" hoặc "Tên sản phẩm" có giá trị khớp hint
        for row in rows[1:6]:
            for col_idx in data_col_indices:
                if col_idx < len(row):
                    cell = row[col_idx].strip().lower()
                    if hint_lower in cell or cell in hint_lower:
                        return col_idx

    return data_col_indices[0]


def _parse_template_gia_tri_chot(rows: list, header: list) -> dict | None:
    """Template 1: cột 'Tên thuộc tính' + cột giá trị.

    Tìm TẤT CẢ cột giá trị có trong sheet, dòng nào có data ở cột nào
    thì lấy — không bỏ sót."""
    label_idx = _find_column_index(header, LABEL_COLUMN_NAMES)
    if label_idx is None:
        return None

    # Tìm tất cả cột giá trị có mặt trong header
    normalized_header = [h.strip().lower() for h in header]
    value_indices = []
    for name in VALUE_COLUMN_NAMES:
        name_norm = name.strip().lower()
        # Exact match
        if name_norm in normalized_header:
            idx = normalized_header.index(name_norm)
            if idx not in value_indices and idx != label_idx:
                value_indices.append(idx)
            continue
        # Contains match
        for i, h in enumerate(normalized_header):
            if name_norm in h and i not in value_indices and i != label_idx:
                value_indices.append(i)
                break

    if not value_indices:
        return None

    specs = {}
    for row in rows[1:]:
        if label_idx >= len(row):
            continue
        label = row[label_idx].strip()
        if not label:
            continue
        # Lấy giá trị từ cột nào có data
        value = ""
        for vi in value_indices:
            if vi < len(row) and row[vi].strip():
                value = row[vi].strip()
                break
        if not value:
            continue
        specs.setdefault(label, value)
    return specs


def _parse_template_pim_multi(rows: list, header: list,
                               product_hint: str | None = None) -> dict | None:
    """Template 2: 'Bảng TSKT đầy đủ' (multi-product PIM).
    Cột A = label, cột B = TRUE/FALSE, cột C+ = giá trị sản phẩm."""
    label_idx = _find_column_index(header, PIM_LABEL_COLUMN_NAMES)
    if label_idx is None:
        return None

    # Tìm các cột dữ liệu (bỏ qua cột boolean)
    data_col_indices = []
    for i in range(len(header)):
        if i == label_idx:
            continue
        if _is_boolean_column(rows, i):
            continue
        data_col_indices.append(i)

    if not data_col_indices:
        return None

    value_idx = _pick_product_column(rows, data_col_indices, product_hint)

    specs = {}
    for row in rows[1:]:
        if len(row) <= max(label_idx, value_idx):
            continue
        label = row[label_idx].strip()
        value = row[value_idx].strip()
        if not label or not value:
            continue
        if _is_section_header(label):
            continue
        # Bỏ dòng hướng dẫn quá dài (VD: "Tiện ích (liệt kê chi tiết...)")
        # giữ label gọn dạng "Tên thuộc tính" thật sự
        # -> cắt phần VD/hướng dẫn trong ngoặc nếu label > 80 ký tự
        clean_label = _clean_pim_label(label)
        if not clean_label:
            continue
        specs.setdefault(clean_label, value)
    return specs


def _clean_pim_label(label: str) -> str:
    """Rút gọn label PIM multi-product: bỏ phần hướng dẫn/ví dụ trong ngoặc
    hoặc sau dấu ' - VD:', ' VD:', giữ lại phần tên thuộc tính ngắn gọn.
    Ví dụ:
      'Loại máy: VD 1 chiều 2 chiều' -> 'Loại máy'
      'Công suất làm lạnh (đơn vị HP - BTU)' -> 'Công suất làm lạnh'
      'Tiêu thụ điện (kWh) (ghi rõ thông số...)' -> 'Tiêu thụ điện (kWh)'
    """
    # Bỏ ngoặc chứa "VD" trước, ví dụ "(VD: R-32)" hoặc "(VD 1 chiều 2 chiều)"
    label = re.sub(r'\(\s*VD\b[^)]*\)', '', label, flags=re.IGNORECASE)

    # Bỏ phần sau dấu ". VD:" hoặc ": VD" hoặc " VD:" (case insensitive)
    label = re.split(r'[.,:]\s*VD\b', label, maxsplit=1, flags=re.IGNORECASE)[0]
    label = re.split(r'\bVD\s*:', label, maxsplit=1, flags=re.IGNORECASE)[0]

    # Bỏ phần hướng dẫn trong ngoặc nếu ngoặc chứa >= 20 ký tự (hướng dẫn),
    # giữ ngoặc ngắn (đơn vị đo, ví dụ "(kWh)", "(mm)")
    label = re.sub(r'\([^)]{20,}\)', '', label)

    # Bỏ phần sau " - " nếu phần sau dài > 20 ký tự (ghi chú/hướng dẫn)
    parts = label.split(' - ', 1)
    if len(parts) == 2 and len(parts[1].strip()) > 20:
        label = parts[0]

    # Bỏ dấu * ở đầu/cuối (marker bắt buộc)
    label = label.strip(' *.:：\t')

    if len(label) > 80:
        return ""
    return label


def _parse_fallback_generic(rows: list, header: list) -> dict | None:
    """Fallback: cột 0 = label, cột cuối có dữ liệu = value.
    Áp dụng khi không nhận ra template nào cụ thể, nhưng sheet có >= 2 cột
    và cột đầu trông giống danh sách thuộc tính."""
    if len(header) < 2:
        return None

    # Chọn cột value: cột cuối cùng có >= 5 dòng không rỗng
    value_idx = None
    for col in range(len(header) - 1, 0, -1):
        filled = sum(1 for row in rows[1:] if col < len(row) and row[col].strip())
        if filled >= 5:
            value_idx = col
            break

    if value_idx is None:
        return None

    # Bỏ qua cột boolean nếu nó nằm ngay cạnh cột label
    if value_idx == 1 and _is_boolean_column(rows, 1) and len(header) > 2:
        value_idx = 2

    specs = {}
    for row in rows[1:]:
        if len(row) <= max(0, value_idx):
            continue
        label = row[0].strip()
        value = row[value_idx].strip()
        if not label or not value:
            continue
        if len(label) > 120:
            continue
        specs.setdefault(label, value)
    return specs


def load_google_sheet_source(url: str,
                              product_hint: str | None = None) -> SheetSource:
    """Tải 1 Google Sheet công khai dạng CSV và tự nhận diện template để
    trích specs. Hỗ trợ cả Google Sheets gốc lẫn .xlsx upload lên Drive.

    product_hint: mã/tên sản phẩm, dùng để chọn đúng cột trong sheet
    multi-product (nếu có). Ví dụ: '370959' hoặc 'SHR-AW09IC620'."""
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
        # Kiểm tra xem HTML có phải trang lỗi "file không tồn tại" không
        body_lower = resp.text[:2000].lower()
        if "không tồn tại" in body_lower or "not found" in body_lower or "404" in body_lower:
            raise RuntimeError(
                "Google Sheet không tồn tại hoặc đã bị xóa/di chuyển — "
                "kiểm tra lại link."
            )
        raise RuntimeError(
            "Google Sheet có vẻ chưa công khai — app nhận về trang đăng "
            "nhập/HTML thay vì dữ liệu CSV. Vào Chia sẻ > Anyone with the "
            "link > Viewer rồi thử lại."
        )

    text = resp.content.decode("utf-8-sig", errors="ignore")

    # Kiểm tra nội dung có thực sự là CSV hay là response lỗi dạng khác
    if not text.strip() or len(text.strip()) < 10:
        raise RuntimeError(
            "Google Sheet trả về dữ liệu rỗng — có thể file không tồn tại, "
            "đã bị xóa, hoặc chưa được chia sẻ công khai."
        )

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("Google Sheet trống hoặc không đọc được dữ liệu.")

    # Sheet chỉ có 1 dòng (header) hoặc quá ít dữ liệu -> có thể lỗi
    if len(rows) <= 1:
        raise RuntimeError(
            "Google Sheet chỉ có 1 dòng (header) — không có dữ liệu thông số. "
            "Kiểm tra lại đúng sheet/tab (gid)."
        )

    header = rows[0]

    # Thử lần lượt các template parser, dùng kết quả đầu tiên thành công
    template_used = None

    # 1) Template "Giá trị chốt"
    specs = _parse_template_gia_tri_chot(rows, header)
    if specs:
        template_used = "Giá trị chốt"

    # 2) Template PIM multi-product ("Bảng TSKT đầy đủ")
    if specs is None:
        specs = _parse_template_pim_multi(rows, header, product_hint)
        if specs:
            template_used = "Bảng TSKT đầy đủ (multi-product)"

    # 3) Fallback chung
    if specs is None:
        specs = _parse_fallback_generic(rows, header)
        if specs:
            template_used = "tự nhận diện (generic)"

    if specs:
        source.specs = specs
        if template_used:
            source.warnings.append(f"Nhận diện template: {template_used}")
    else:
        # Gợi ý chi tiết hơn dựa trên nội dung thực tế
        header_preview = ", ".join(h[:30] for h in header[:4] if h.strip())
        source.warnings.append(
            f"Không nhận diện được template thông số trong sheet "
            f"({len(rows)} dòng, header: [{header_preview}]). "
            "Kiểm tra: (1) link đúng sheet/tab chứa dữ liệu? "
            "(2) file vẫn tồn tại và được chia sẻ công khai?"
        )

    return source
