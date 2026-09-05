"""
image_compare.py
So sánh ảnh sản phẩm giữa 2 nguồn (bài viết TGDĐ/ĐMX vs trang hãng) bằng
perceptual hash (phash), để phát hiện ảnh minh hoạ sai sản phẩm hoặc thiếu
ảnh quan trọng.

Cách làm: với mỗi ảnh bên A, tìm ảnh bên B có phash gần nhất (Hamming
distance nhỏ nhất). Không giả định 2 danh sách ảnh cùng thứ tự hay cùng số
lượng.
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
    url_a: str
    url_b: str
    distance: int | None
    status: str   # "Khớp" | "Nghi ngờ" | "Không khớp" | "Lỗi tải ảnh"
    error: str = ""


def _download_image(url: str):
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def _compute_phash_map(urls: list) -> dict:
    """Tải từng ảnh và tính phash, bỏ qua (kèm cảnh báo) nếu ảnh lỗi."""
    hashes = {}
    errors = {}
    for url in urls:
        try:
            img = _download_image(url)
            hashes[url] = imagehash.phash(img)
        except Exception as exc:  # noqa: BLE001 - ảnh lỗi không nên làm sập cả app
            errors[url] = str(exc)
    return hashes, errors


def compare_image_sets(urls_a: list, urls_b: list,
                        match_distance: int = MATCH_DISTANCE,
                        suspect_distance: int = SUSPECT_DISTANCE) -> list:
    """So sánh 2 danh sách URL ảnh. Với mỗi ảnh bên A, ghép với ảnh bên B có
    khoảng cách phash nhỏ nhất còn chưa được ghép (greedy, ưu tiên theo thứ
    tự ảnh bên A xuất hiện trước — thường là ảnh đại diện/quan trọng nhất)."""
    hashes_a, errors_a = _compute_phash_map(urls_a)
    hashes_b, errors_b = _compute_phash_map(urls_b)

    results: list = []
    used_b = set()

    for url_a in urls_a:
        if url_a in errors_a:
            results.append(ImageMatchResult(
                url_a=url_a, url_b="", distance=None,
                status="Lỗi tải ảnh", error=errors_a[url_a],
            ))
            continue

        hash_a = hashes_a[url_a]
        best_url_b, best_distance = None, None

        for url_b, hash_b in hashes_b.items():
            if url_b in used_b:
                continue
            distance = hash_a - hash_b
            if best_distance is None or distance < best_distance:
                best_distance, best_url_b = distance, url_b

        if best_url_b is None:
            results.append(ImageMatchResult(
                url_a=url_a, url_b="", distance=None,
                status="Không khớp", error="Không còn ảnh nào bên trang hãng để so.",
            ))
            continue

        used_b.add(best_url_b)
        if best_distance <= match_distance:
            status = "Khớp"
        elif best_distance <= suspect_distance:
            status = "Nghi ngờ"
        else:
            status = "Không khớp"

        results.append(ImageMatchResult(
            url_a=url_a, url_b=best_url_b, distance=best_distance, status=status,
        ))

    for url_b, err in errors_b.items():
        results.append(ImageMatchResult(
            url_a="", url_b=url_b, distance=None, status="Lỗi tải ảnh", error=err,
        ))

    return results
