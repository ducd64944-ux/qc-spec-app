"""
matcher.py
Fuzzy matching CŨ — dùng làm fallback khi CHƯA có file mapping_thuoc_tinh.xlsx
trong repo (xem attribute_mapping.py cho cách đối chiếu theo ID, là cách
chính được khuyến nghị).

Ý tưởng:
  1. So khớp TÊN thuộc tính giữa 2 nguồn bằng fuzzy string match (rapidfuzz),
     sau khi đã bỏ dấu tiếng Việt để tăng độ chính xác.
  2. Với mỗi cặp thuộc tính đã khớp tên, so khớp GIÁ TRỊ sau khi chuẩn hoá
     số liệu/đơn vị đo (vd "55 inch" ~ "55inch" ~ "55\"").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

LABEL_MATCH_THRESHOLD = 55   # điểm fuzzy tối thiểu (0-100) để coi là cùng thuộc tính
VALUE_MATCH_THRESHOLD = 85   # điểm fuzzy tối thiểu để coi là giá trị khớp


@dataclass
class SpecComparisonRow:
    label_a: str
    label_b: str
    value_a: str
    value_b: str
    status: str  # "Khớp" | "Lệch" | "Nghi ngờ" | "Thiếu"
    label_score: float = 0.0
    value_score: float = 0.0


def strip_vietnamese_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    return text


def _normalize_label(label: str) -> str:
    label = strip_vietnamese_accents(label).lower()
    label = re.sub(r"[^a-z0-9 ]", " ", label)
    label = re.sub(r"\s+", " ", label).strip()
    return label


_UNIT_ALIASES = {
    "inch": ["inch", "in", '"', "″"],
    "gb": ["gb", "gigabyte", "gigabytes"],
    "mb": ["mb", "megabyte", "megabytes"],
    "kg": ["kg", "kilogram", "kilograms"],
    "g": ["gam", "gram", "grams"],
    "mm": ["mm", "milimet", "millimeter"],
    "cm": ["cm", "centimet", "centimeter"],
    "w": ["w", "watt", "watts"],
    "mah": ["mah"],
    "hz": ["hz", "hertz"],
    "l": ["lit", "liter", "litre"],
}


def normalize_value(value: str) -> str:
    """Chuẩn hoá giá trị để so sánh: bỏ dấu, hạ chữ thường, gộp khoảng
    trắng, chuẩn hoá vài đơn vị đo phổ biến, bỏ dấu phẩy ngăn cách nghìn."""
    v = strip_vietnamese_accents(value).lower().strip()
    v = v.replace(",", "")
    v = re.sub(r"\s+", " ", v)

    for canonical, aliases in _UNIT_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            v = re.sub(rf"(?<=\d)\s*{re.escape(alias)}\b", canonical, v)

    v = re.sub(r"\s+", "", v)
    return v


def match_specs_fuzzy(specs_a: dict, specs_b: dict) -> list:
    """So khớp 2 dict thông số {label: value} bằng fuzzy match tên thuộc
    tính. Trả về danh sách SpecComparisonRow.

    specs_a: thông số từ bài viết TGDĐ/ĐMX
    specs_b: thông số từ trang hãng
    """
    rows: list = []
    used_b_keys = set()

    norm_b = {k: _normalize_label(k) for k in specs_b}

    for label_a, value_a in specs_a.items():
        norm_a = _normalize_label(label_a)
        best_key, best_score = None, 0.0

        for label_b, nb in norm_b.items():
            if label_b in used_b_keys:
                continue
            score = fuzz.token_sort_ratio(norm_a, nb)
            if score > best_score:
                best_score, best_key = score, label_b

        if best_key is not None and best_score >= LABEL_MATCH_THRESHOLD:
            used_b_keys.add(best_key)
            value_b = specs_b[best_key]
            value_score = fuzz.ratio(normalize_value(value_a), normalize_value(value_b))
            if value_score >= VALUE_MATCH_THRESHOLD:
                status = "Khớp"
            elif value_score >= VALUE_MATCH_THRESHOLD - 25:
                status = "Nghi ngờ"
            else:
                status = "Lệch"
            rows.append(
                SpecComparisonRow(
                    label_a=label_a,
                    label_b=best_key,
                    value_a=value_a,
                    value_b=value_b,
                    status=status,
                    label_score=best_score,
                    value_score=value_score,
                )
            )
        else:
            rows.append(
                SpecComparisonRow(
                    label_a=label_a,
                    label_b="",
                    value_a=value_a,
                    value_b="",
                    status="Thiếu",
                    label_score=best_score,
                    value_score=0.0,
                )
            )

    for label_b, value_b in specs_b.items():
        if label_b in used_b_keys:
            continue
        rows.append(
            SpecComparisonRow(
                label_a="",
                label_b=label_b,
                value_a="",
                value_b=value_b,
                status="Thiếu",
            )
        )

    return rows
