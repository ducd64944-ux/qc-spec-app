"""
local_source.py
Xử lý "nguồn đối chiếu" do Đức tự upload trực tiếp (ảnh/pdf/xlsx/csv/txt) khi
sản phẩm không có link trang hãng rõ ràng để cào tự động.

Mục tiêu: từ 1 nhóm file upload (có thể trộn nhiều loại — ví dụ vừa có ảnh
chụp vừa có 1 file pdf catalogue), tạo ra 1 "nguồn" tương đương với 1
ScrapedPage đã cào từ link: có specs (dict {label: value}) và images (list
ảnh, ở đây là bytes thay vì URL).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO

import pandas as pd

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
SPREADSHEET_EXTENSIONS = (".xlsx", ".xls", ".csv")
TEXT_EXTENSIONS = (".txt",)
PDF_EXTENSIONS = (".pdf",)


@dataclass
class LocalSource:
    specs: dict = field(default_factory=dict)
    images: list = field(default_factory=list)  # list of (label, bytes)
    warnings: list = field(default_factory=list)


_LABEL_VALUE_PATTERN = re.compile(r"^(?P<label>[^:：]{2,60})[:：]\s*(?P<value>.+)$")


def _clean_pair(label: str, value: str) -> tuple:
    label = re.sub(r"\s+", " ", label).strip(" .:：\t")
    value = re.sub(r"\s+", " ", value).strip(" .:：\t")
    if not label or not value:
        return "", ""
    if label.lower() == value.lower():
        return "", ""
    if len(label) > 80:
        return "", ""
    return label, value


def _parse_label_value_lines(text: str) -> dict:
    """Parse các dòng dạng 'Label: Value' trong text thuần (dùng cho .txt và
    text trích từ PDF)."""
    specs: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) > 200:
            continue
        match = _LABEL_VALUE_PATTERN.match(line)
        if match:
            label, value = _clean_pair(match.group("label"), match.group("value"))
            if label and value:
                specs.setdefault(label, value)
    return specs


def _parse_pdf(data: bytes, filename: str) -> tuple:
    """Trích text (dạng Label: Value) + ảnh từ file PDF bằng pypdf."""
    specs: dict = {}
    images: list = []
    warnings: list = []
    try:
        from pypdf import PdfReader
    except ImportError:
        warnings.append(f"Thiếu thư viện pypdf để đọc file PDF '{filename}'.")
        return specs, images, warnings

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Không đọc được file PDF '{filename}': {exc}")
        return specs, images, warnings

    for page_idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        specs.update(_parse_label_value_lines(text))

        try:
            page_images = page.images
        except Exception:  # noqa: BLE001
            page_images = []
        for img_idx, img in enumerate(page_images):
            try:
                images.append((f"{filename} (trang {page_idx + 1}, ảnh {img_idx + 1})", img.data))
            except Exception:  # noqa: BLE001
                continue

    if not specs:
        warnings.append(f"Không tìm thấy dòng 'Label: Value' nào trong text của '{filename}'.")
    if not images:
        warnings.append(f"Không tìm thấy ảnh nào trong file PDF '{filename}'.")

    return specs, images, warnings


def _parse_spreadsheet(data: bytes, filename: str) -> dict:
    """Đọc file xlsx/xls/csv: dùng 2 cột đầu tiên (không tính header nếu có
    vẻ là header) làm label/value."""
    specs: dict = {}
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(BytesIO(data), header=None, dtype=str)
        else:
            df = pd.read_excel(BytesIO(data), header=None, dtype=str)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Không đọc được file '{filename}': {exc}") from exc

    if df.shape[1] < 2:
        return specs

    for _, row in df.iterrows():
        label = row.iloc[0]
        value = row.iloc[1]
        if pd.isna(label) or pd.isna(value):
            continue
        label, value = _clean_pair(str(label), str(value))
        if label and value:
            specs.setdefault(label, value)

    return specs


def load_local_source(files: list) -> LocalSource:
    """files: danh sách object có .name (str) và .getvalue() trả bytes — đúng
    interface của st.uploaded_file_manager.UploadedFile (Streamlit). Gộp tất
    cả file thuộc 1 sản phẩm thành 1 LocalSource duy nhất."""
    source = LocalSource()

    for f in files:
        filename = getattr(f, "name", "file")
        try:
            data = f.getvalue()
        except Exception as exc:  # noqa: BLE001
            source.warnings.append(f"Không đọc được file '{filename}': {exc}")
            continue

        lower_name = filename.lower()

        if lower_name.endswith(IMAGE_EXTENSIONS):
            source.images.append((filename, data))
            continue

        if lower_name.endswith(PDF_EXTENSIONS):
            specs, images, warnings = _parse_pdf(data, filename)
            source.specs.update(specs)
            source.images.extend(images)
            source.warnings.extend(warnings)
            continue

        if lower_name.endswith(SPREADSHEET_EXTENSIONS):
            try:
                specs = _parse_spreadsheet(data, filename)
                source.specs.update(specs)
                if not specs:
                    source.warnings.append(f"Không tìm thấy dữ liệu label/value nào trong '{filename}'.")
            except RuntimeError as exc:
                source.warnings.append(str(exc))
            continue

        if lower_name.endswith(TEXT_EXTENSIONS):
            try:
                text = data.decode("utf-8", errors="ignore")
            except Exception as exc:  # noqa: BLE001
                source.warnings.append(f"Không đọc được file text '{filename}': {exc}")
                continue
            specs = _parse_label_value_lines(text)
            source.specs.update(specs)
            if not specs:
                source.warnings.append(f"Không tìm thấy dòng 'Label: Value' nào trong '{filename}'.")
            continue

        source.warnings.append(f"Không hỗ trợ định dạng file '{filename}' — bỏ qua.")

    if not source.specs:
        source.warnings.append("Không trích được thông số kỹ thuật nào từ các file đã upload.")
    if not source.images:
        source.warnings.append("Không có ảnh nào trong các file đã upload.")

    return source
