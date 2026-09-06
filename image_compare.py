"""
image_compare.py
So sánh ảnh sản phẩm giữa 2 nguồn bằng perceptual hash (phash), để phát hiện
ảnh minh hoạ sai sản phẩm hoặc thiếu ảnh quan trọng.

Mỗi "nguồn ảnh" trong danh sách truyền vào có thể là:
  - 1 URL (str) — sẽ được tải về qua HTTP (dùng cho ảnh cào từ bài viết
    TGDĐ/ĐMX hoặc từ trang hãng).
  - 1 tuple (nhãn, bytes) — ảnh đã có sẵn dữ liệu nhị phân, dùng cho ảnh
    trong file Đức tự upload (ảnh chụp, ảnh trích từ PDF...), không cần
    tải qua mạng.

Cách làm: với mỗi ảnh bên A, tìm ảnh bên B có phash gần nhất (Hamming
distance nhỏ nhất). Không giả định 2 danh sách ảnh cùng thứ tự hay cùng số
lượng, và 2 bên có thể trộn lẫn URL và ảnh upload.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import imagehash
import requests
from PIL import Image

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20

MATCH_DISTANCE = 8        # Hamming distance <= giá trị này -> coi là "Khớp"
SUSPECT_DISTANCE = 16      # <= giá trị này -> "Nghi ngờ", lớn hơn -> "Không khớp"


@dataclass
class ImageMatchResult:
    ref_a: object          # URL (str) hoặc bytes — truyền thẳng được vào st.image
    ref_b: object
    label_a: str           # tên hiển thị (URL rút gọn, hoặc tên file upload)
    label_b: str
    distance: int | None
    status: str            # "Khớp" | "Nghi ngờ" | "Không khớp" | "Lỗi tải ảnh"
    error: str = ""


def _image_label(source) -> str:
    if isinstance(source, tuple):
        return source[0]
    return str(source)


def _load_image(source) -> Image.Image:
    """source: URL (str) hoặc tuple (nhãn, bytes)."""
    if isinstance(source, tuple):
        _, data = source
        return Image.open(io.BytesIO(data)).convert("RGB")
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source)).convert("RGB")
    if isinstance(source, str):
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(source, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    raise TypeError(f"Không hỗ trợ kiểu nguồn ảnh: {type(source)}")


def _compute_phash_list(sources: list) -> tuple:
    """Tính phash cho từng ảnh trong danh sách theo thứ tự (index). Trả về
    (hashes: {index: hash}, errors: {index: str})."""
    hashes: dict = {}
    errors: dict = {}
    for idx, source in enumerate(sources):
        try:
            img = _load_image(source)
            hashes[idx] = imagehash.phash(img)
        except Exception as exc:  # noqa: BLE001 - ảnh lỗi không nên làm sập cả app
            errors[idx] = str(exc)
    return hashes, errors


def compare_image_sets(sources_a: list, sources_b: list,
                        match_distance: int = MATCH_DISTANCE,
                        suspect_distance: int = SUSPECT_DISTANCE) -> list:
    """So sánh 2 danh sách nguồn ảnh (mỗi phần tử là URL hoặc tuple (nhãn,
    bytes)). Với mỗi ảnh bên A, ghép với ảnh bên B có khoảng cách phash nhỏ
    nhất còn chưa được ghép (greedy, ưu tiên theo thứ tự ảnh bên A xuất hiện
    trước — thường là ảnh đại diện/quan trọng nhất)."""
    hashes_a, errors_a = _compute_phash_list(sources_a)
    hashes_b, errors_b = _compute_phash_list(sources_b)

    results: list = []
    used_b = set()

    for i, source_a in enumerate(sources_a):
        if i in errors_a:
            results.append(ImageMatchResult(
                ref_a=source_a, ref_b=None,
                label_a=_image_label(source_a), label_b="",
                distance=None, status="Lỗi tải ảnh", error=errors_a[i],
            ))
            continue

        hash_a = hashes_a[i]
        best_j, best_distance = None, None
        for j, hash_b in hashes_b.items():
            if j in used_b:
                continue
            distance = hash_a - hash_b
            if best_distance is None or distance < best_distance:
                best_distance, best_j = distance, j

        if best_j is None:
            results.append(ImageMatchResult(
                ref_a=source_a, ref_b=None,
                label_a=_image_label(source_a), label_b="",
                distance=None, status="Không khớp",
                error="Không còn ảnh nào bên nguồn đối chiếu để so.",
            ))
            continue

        used_b.add(best_j)
        source_b = sources_b[best_j]
        if best_distance <= match_distance:
            status = "Khớp"
        elif best_distance <= suspect_distance:
            status = "Nghi ngờ"
        else:
            status = "Không khớp"

        results.append(ImageMatchResult(
            ref_a=source_a, ref_b=source_b,
            label_a=_image_label(source_a), label_b=_image_label(source_b),
            distance=best_distance, status=status,
        ))

    for j, err in errors_b.items():
        results.append(ImageMatchResult(
            ref_a=None, ref_b=sources_b[j],
            label_a="", label_b=_image_label(sources_b[j]),
            distance=None, status="Lỗi tải ảnh", error=err,
        ))

    return results
