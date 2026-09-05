"""
attribute_mapping.py
Đối chiếu thông số kỹ thuật theo BẢNG MAPPING ID thuộc tính — cách tiếp cận
chính (thay cho fuzzy-only trong matcher.py), lấy cảm hứng từ sheet
"MAPPING TSKT MOI" trong tool 66.py (PIM_Data workspace) của Đức.

Cấu trúc file mapping_thuoc_tinh.xlsx (đặt cố định cùng thư mục app.py,
trong repo GitHub — app tự đọc mỗi lần chạy):

    ID | Ten_chuan | Bien_the_DMX | Bien_the_Hang

    - ID: mã định danh thuộc tính, do Đức tự đặt (vd "TT01").
    - Ten_chuan: tên hiển thị chuẩn của thuộc tính (vd "Kích thước màn hình").
    - Bien_the_DMX: các cách gọi thuộc tính này từng thấy trên bài viết
      TGDĐ/ĐMX, cách nhau bằng dấu ";" (vd "Kích thước màn hình;Màn hình").
    - Bien_the_Hang: các cách gọi thuộc tính này từng thấy trên trang hãng,
      cách nhau bằng ";" (vd "Screen Size;Display Size").

Nếu thiếu file này trong repo, app dùng matcher.py (fuzzy-only) và cảnh báo.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import pandas as pd
from rapidfuzz import fuzz

from matcher import normalize_value, strip_vietnamese_accents

DEFAULT_MAPPING_FILENAME = "mapping_thuoc_tinh.xlsx"
REQUIRED_COLUMNS = ["ID", "Ten_chuan", "Bien_the_DMX", "Bien_the_Hang"]

FUZZY_FALLBACK_THRESHOLD = 80  # điểm fuzzy tối thiểu để coi 1 nhãn "khớp" 1 alias đã khai báo


@dataclass
class MappingLoadResult:
    dataframe: object = None       # pandas.DataFrame hoặc None nếu không đọc được
    source_path: str = ""
    ok: bool = False
    error: str = ""


@dataclass
class IdMatchRow:
    attribute_id: str
    ten_chuan: str
    label_a: str
    label_b: str
    value_a: str
    value_b: str
    status: str            # "Khớp" | "Lệch" | "Nghi ngờ" | "Thiếu"
    match_method: str       # "id_exact" | "id_fuzzy_alias"
    value_score: float = 0.0


@dataclass
class UnmappedAttribute:
    side: str          # "DMX" | "Hang"
    label: str
    value: str


# ---------------------------------------------------------------------------
# Đọc file mapping mặc định từ repo
# ---------------------------------------------------------------------------

def load_default_mapping(app_dir: str | None = None,
                          filename: str = DEFAULT_MAPPING_FILENAME) -> MappingLoadResult:
    """Đọc mapping_thuoc_tinh.xlsx nằm cùng thư mục với app.py trong repo.

    app_dir: thư mục chứa app.py (thường truyền os.path.dirname(__file__)).
             Nếu None, dùng thư mục làm việc hiện tại.
    """
    base_dir = app_dir if app_dir else os.getcwd()
    path = os.path.join(base_dir, filename)

    if not os.path.isfile(path):
        return MappingLoadResult(ok=False, source_path=path,
                                  error=f"Không tìm thấy file '{filename}' trong repo.")

    try:
        df = pd.read_excel(path, dtype=str).fillna("")
    except Exception as exc:  # noqa: BLE001 - muốn báo lỗi đọc file rõ ràng cho người dùng
        return MappingLoadResult(ok=False, source_path=path, error=f"Lỗi đọc file mapping: {exc}")

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        return MappingLoadResult(
            ok=False, source_path=path,
            error=f"File mapping thiếu cột: {', '.join(missing_cols)}",
        )

    return MappingLoadResult(dataframe=df, source_path=path, ok=True)


def load_mapping_from_upload(uploaded_file) -> MappingLoadResult:
    """Đọc mapping từ file người dùng upload tạm thời (override), dùng cho
    khu 'Quản lý mapping' trong app.py khi Đức muốn thử 1 bản mapping khác
    trước khi merge/commit vào repo."""
    try:
        df = pd.read_excel(uploaded_file, dtype=str).fillna("")
    except Exception as exc:  # noqa: BLE001
        return MappingLoadResult(ok=False, error=f"Lỗi đọc file mapping đã upload: {exc}")

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        return MappingLoadResult(ok=False, error=f"File mapping thiếu cột: {', '.join(missing_cols)}")

    return MappingLoadResult(dataframe=df, source_path="(uploaded)", ok=True)


# ---------------------------------------------------------------------------
# Xây chỉ mục alias -> ID
# ---------------------------------------------------------------------------

def _split_aliases(cell: str) -> list:
    if not cell:
        return []
    return [a.strip() for a in str(cell).split(";") if a.strip()]


def _normalize_alias(text: str) -> str:
    text = strip_vietnamese_accents(str(text)).lower().strip()
    return " ".join(text.split())


def _build_alias_index(df, alias_column: str) -> dict:
    """Trả về {alias_đã_chuẩn_hoá: (id, ten_chuan, alias_gốc)}."""
    index = {}
    for _, row in df.iterrows():
        attribute_id = str(row["ID"]).strip()
        ten_chuan = str(row["Ten_chuan"]).strip()
        if not attribute_id:
            continue
        aliases = _split_aliases(row[alias_column])
        if ten_chuan:
            aliases.append(ten_chuan)
        for alias in aliases:
            norm = _normalize_alias(alias)
            if norm and norm not in index:
                index[norm] = (attribute_id, ten_chuan, alias)
    return index


def _lookup_id_for_label(label: str, alias_index: dict) -> tuple:
    """Tìm ID cho 1 nhãn thuộc tính: thử exact match trước, sau đó fuzzy
    fallback TRONG PHẠM VI các alias đã khai báo (không fuzzy tự do như
    matcher.py) để tránh nhận nhầm.

    Trả về (attribute_id, ten_chuan, match_method) hoặc (None, None, None).
    """
    norm_label = _normalize_alias(label)
    if norm_label in alias_index:
        attribute_id, ten_chuan, _ = alias_index[norm_label]
        return attribute_id, ten_chuan, "id_exact"

    best_score, best_entry = 0.0, None
    for alias_norm, entry in alias_index.items():
        score = fuzz.ratio(norm_label, alias_norm)
        if score > best_score:
            best_score, best_entry = score, entry

    if best_entry is not None and best_score >= FUZZY_FALLBACK_THRESHOLD:
        attribute_id, ten_chuan, _ = best_entry
        return attribute_id, ten_chuan, "id_fuzzy_alias"

    return None, None, None


# ---------------------------------------------------------------------------
# Đối chiếu chính
# ---------------------------------------------------------------------------

def match_specs_by_id(specs_a: dict, specs_b: dict, mapping_df,
                       value_match_threshold: int = 85,
                       value_suspect_threshold: int = 60) -> tuple:
    """Đối chiếu thông số theo ID.

    specs_a: {label: value} trích từ bài viết TGDĐ/ĐMX
    specs_b: {label: value} trích từ trang hãng
    mapping_df: DataFrame mapping (từ load_default_mapping/load_mapping_from_upload)

    Trả về (rows: list[IdMatchRow], unmapped: list[UnmappedAttribute])
    """
    alias_index_a = _build_alias_index(mapping_df, "Bien_the_DMX")
    alias_index_b = _build_alias_index(mapping_df, "Bien_the_Hang")

    matched_by_id: dict = {}   # id -> dict(ten_chuan,label_a,value_a,label_b,value_b,method)
    unmapped: list = []

    for label_a, value_a in specs_a.items():
        attribute_id, ten_chuan, method = _lookup_id_for_label(label_a, alias_index_a)
        if attribute_id is None:
            unmapped.append(UnmappedAttribute(side="DMX", label=label_a, value=value_a))
            continue
        entry = matched_by_id.setdefault(attribute_id, {
            "ten_chuan": ten_chuan, "label_a": "", "value_a": "",
            "label_b": "", "value_b": "", "method": method,
        })
        entry["label_a"], entry["value_a"] = label_a, value_a

    for label_b, value_b in specs_b.items():
        attribute_id, ten_chuan, method = _lookup_id_for_label(label_b, alias_index_b)
        if attribute_id is None:
            unmapped.append(UnmappedAttribute(side="Hang", label=label_b, value=value_b))
            continue
        entry = matched_by_id.setdefault(attribute_id, {
            "ten_chuan": ten_chuan, "label_a": "", "value_a": "",
            "label_b": "", "value_b": "", "method": method,
        })
        entry["label_b"], entry["value_b"] = label_b, value_b
        if not entry.get("ten_chuan"):
            entry["ten_chuan"] = ten_chuan

    rows: list = []
    for attribute_id, entry in matched_by_id.items():
        label_a, value_a = entry["label_a"], entry["value_a"]
        label_b, value_b = entry["label_b"], entry["value_b"]

        if value_a and value_b:
            score = fuzz.ratio(normalize_value(value_a), normalize_value(value_b))
            if score >= value_match_threshold:
                status = "Khớp"
            elif score >= value_suspect_threshold:
                status = "Nghi ngờ"
            else:
                status = "Lệch"
        else:
            score = 0.0
            status = "Thiếu"

        rows.append(IdMatchRow(
            attribute_id=attribute_id,
            ten_chuan=entry["ten_chuan"],
            label_a=label_a, label_b=label_b,
            value_a=value_a, value_b=value_b,
            status=status, match_method=entry["method"],
            value_score=score,
        ))

    rows.sort(key=lambda r: r.attribute_id)
    return rows, unmapped


# ---------------------------------------------------------------------------
# Sinh file mapping cập nhật kèm gợi ý dòng mới
# ---------------------------------------------------------------------------

def build_updated_mapping_with_suggestions(mapping_df, unmapped: list) -> bytes:
    """Gộp mapping hiện tại với các thuộc tính CHƯA có trong mapping (tìm
    thấy trong lần chạy này), sinh ra 1 file .xlsx mới (ID để trống) để Đức
    tải về, điền ID rồi merge/commit đè vào repo.

    Nếu 1 label đã trùng với alias đã có (không phân biệt hoa/thường,
    dấu câu) thì không gợi ý lại.
    """
    existing_aliases_dmx = set(_build_alias_index(mapping_df, "Bien_the_DMX").keys())
    existing_aliases_hang = set(_build_alias_index(mapping_df, "Bien_the_Hang").keys())

    new_rows = []
    seen_labels = set()
    for item in unmapped:
        norm = _normalize_alias(item.label)
        if item.side == "DMX" and norm in existing_aliases_dmx:
            continue
        if item.side == "Hang" and norm in existing_aliases_hang:
            continue
        key = (item.side, norm)
        if key in seen_labels:
            continue
        seen_labels.add(key)

        new_rows.append({
            "ID": "",
            "Ten_chuan": "",
            "Bien_the_DMX": item.label if item.side == "DMX" else "",
            "Bien_the_Hang": item.label if item.side == "Hang" else "",
        })

    combined = pd.concat(
        [mapping_df[REQUIRED_COLUMNS], pd.DataFrame(new_rows, columns=REQUIRED_COLUMNS)],
        ignore_index=True,
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        combined.to_excel(writer, index=False, sheet_name="mapping_thuoc_tinh")
    buffer.seek(0)
    return buffer.read()
