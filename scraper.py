"""
scraper.py
Cào (scrape) một trang sản phẩm bất kỳ (TGDĐ/ĐMX hoặc trang hãng) để lấy:
  1. Bảng thông số kỹ thuật dạng {label: value}
  2. Danh sách ảnh sản phẩm (đã lọc bớt logo/icon/banner)

Thiết kế "generic": không phụ thuộc vào 1 cấu trúc HTML cố định vì:
  - Trang TGDĐ/ĐMX và trang các hãng (Samsung, LG, Sony, Xiaomi...) đều khác
    nhau về markup.
  - Cùng một trang cũng có thể đổi cấu trúc theo thời gian.

Chiến lược: thử lần lượt nhiều "chiến thuật" trích xuất, gộp kết quả lại,
dòng nào trùng label thì giữ giá trị trích được đầu tiên (ưu tiên chiến
thuật đứng trước trong danh sách _EXTRACT_STRATEGIES).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 2

# Từ khoá tiếng Việt/Anh dùng để định vị khu vực "Thông số kỹ thuật" trên trang.
SPEC_SECTION_KEYWORDS = [
    "thông số kỹ thuật",
    "thong so ky thuat",
    "thông số sản phẩm",
    "specifications",
    "specification",
    "technical specs",
    "tech specs",
    "product specs",
    "chi tiết sản phẩm",
    "chi tiết kỹ thuật",
]

# Từ khoá trong URL ảnh cho biết đây là logo/icon/banner, không phải ảnh
# sản phẩm thật -> loại bỏ khỏi danh sách ảnh so sánh.
IMAGE_URL_BLOCKLIST_KEYWORDS = [
    "logo", "icon", "sprite", "favicon", "banner", "background", "bg-",
    "avatar", "badge", "sticker", "voucher", "promo", "campaign",
    "placeholder", "loading", "spinner", "pixel", "tracking", "beacon",
    "social", "facebook", "zalo", "share-", "qr-code", "qrcode",
]

MIN_IMAGE_DIMENSION_HINT = 200  # px, dùng khi HTML có khai báo width/height


@dataclass
class ScrapedPage:
    url: str
    title: str = ""
    specs: dict = field(default_factory=dict)
    images: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    raw_html: str = ""


def fetch_html(url: str) -> str:
    """Tải HTML thô của trang, có retry nhẹ khi lỗi mạng tạm thời."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except requests.RequestException as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Không tải được trang '{url}': {last_err}")


def scrape_page(url: str) -> ScrapedPage:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    page = ScrapedPage(url=url, raw_html=html)

    title_tag = soup.find("title")
    page.title = title_tag.get_text(strip=True) if title_tag else ""

    page.specs = _extract_specs(soup)
    if not page.specs:
        page.warnings.append(
            "Không tìm thấy bảng thông số kỹ thuật nào trên trang này — "
            "có thể trang dùng cấu trúc lạ hoặc load thông số bằng JavaScript."
        )

    page.images = _extract_images(soup, url)
    if not page.images:
        page.warnings.append("Không tìm thấy ảnh sản phẩm nào (sau khi lọc logo/icon).")

    return page


# ---------------------------------------------------------------------------
# Trích thông số kỹ thuật
# ---------------------------------------------------------------------------

def _extract_specs(soup: BeautifulSoup) -> dict:
    specs: dict = {}

    section_roots = _find_spec_section_roots(soup)
    search_roots = section_roots if section_roots else [soup]

    for root in search_roots:
        for label, value in _extract_from_tables(root):
            specs.setdefault(label, value)
        for label, value in _extract_from_definition_lists(root):
            specs.setdefault(label, value)
        for label, value in _extract_from_label_value_text(root):
            specs.setdefault(label, value)

    return specs


def _find_spec_section_roots(soup: BeautifulSoup) -> list:
    """Tìm các khối cha có khả năng chứa bảng thông số, dựa vào heading/từ khoá."""
    roots = []
    seen_ids = set()

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b", "span", "div", "p"]):
        text = tag.get_text(" ", strip=True).lower()
        if not text or len(text) > 60:
            continue
        if any(kw in text for kw in SPEC_SECTION_KEYWORDS):
            container = _closest_reasonable_container(tag)
            if container is not None and id(container) not in seen_ids:
                seen_ids.add(id(container))
                roots.append(container)

    return roots


def _closest_reasonable_container(tag) -> object | None:
    """Từ 1 heading/label 'Thông số kỹ thuật', đi lên tìm khối cha chứa nội
    dung thực sự (bảng/list), tránh lấy nhầm cả <body>."""
    node = tag
    for _ in range(6):
        sibling_content = node.find_next_sibling()
        if sibling_content is not None and sibling_content.find(["table", "dl", "li", "tr"]):
            return sibling_content
        parent = node.parent
        if parent is None:
            break
        if parent.find(["table", "dl"]) or len(parent.find_all("li")) >= 2:
            return parent
        node = parent
    return tag.parent or tag


def _tags_including_self(root, names) -> list:
    """Như root.find_all(names) nhưng tính cả root nếu root khớp names.
    Cần thiết vì _closest_reasonable_container có thể trả về chính thẻ
    <table>/<dl> chứa thông số, và BeautifulSoup.find_all() không tính
    chính thẻ gọi nó."""
    tags = []
    if getattr(root, "name", None) in names:
        tags.append(root)
    tags.extend(root.find_all(names))
    return tags


def _extract_from_tables(root) -> list:
    results = []
    for table in _tags_including_self(root, ["table"]):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                label = cells[0].get_text(" ", strip=True)
                value = cells[1].get_text(" ", strip=True)
                label, value = _clean_pair(label, value)
                if label and value:
                    results.append((label, value))
    return results


def _extract_from_definition_lists(root) -> list:
    results = []
    for dl in _tags_including_self(root, ["dl"]):
        terms = dl.find_all("dt")
        for dt in terms:
            dd = dt.find_next_sibling("dd")
            if dd is None:
                continue
            label, value = _clean_pair(
                dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)
            )
            if label and value:
                results.append((label, value))
    return results


_LABEL_VALUE_PATTERN = re.compile(r"^(?P<label>[^:：]{2,60})[:：]\s*(?P<value>.+)$")


def _extract_from_label_value_text(root) -> list:
    """Bắt các dòng dạng text tự do 'Label: Value' bên trong li/p/div (không
    nằm trong table/dl để tránh trích trùng, và không phải là 1 khối div bọc
    ngoài chứa cả 1 danh sách ul/li nhiều thuộc tính — nếu không, text của cả
    khối bị nối lại và match nhầm thành 1 cặp label/value rác, ví dụ 1 <div>
    bọc ngoài 1 <ul> gồm nhiều <li> "Label: Value" khác nhau)."""
    results = []
    for tag in _tags_including_self(root, ["li", "p", "div"]):
        if tag.find(["table", "dl"]):
            continue  # để _extract_from_tables/_definition_lists xử lý
        if tag.name != "li" and tag.find(["ul", "ol", "li"]):
            continue  # khối bọc ngoài 1 danh sách -> để xử lý ở từng <li> riêng
        text = tag.get_text(" ", strip=True)
        if not text or len(text) > 200:
            continue
        match = _LABEL_VALUE_PATTERN.match(text)
        if match:
            label, value = _clean_pair(match.group("label"), match.group("value"))
            if label and value:
                results.append((label, value))
    return results


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


# ---------------------------------------------------------------------------
# Trích ảnh sản phẩm
# ---------------------------------------------------------------------------

def _extract_images(soup: BeautifulSoup, base_url: str) -> list:
    urls: list = []
    seen = set()

    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        candidate = urljoin(base_url, og_image["content"])
        if candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            srcset = img.get("srcset") or img.get("data-srcset")
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]
        if not src:
            continue

        candidate = urljoin(base_url, src)
        if candidate in seen:
            continue
        if _looks_like_non_product_image(candidate, img):
            continue

        seen.add(candidate)
        urls.append(candidate)

    return urls


def _looks_like_non_product_image(url: str, img_tag) -> bool:
    path = urlparse(url).path.lower()
    if any(kw in path for kw in IMAGE_URL_BLOCKLIST_KEYWORDS):
        return True
    if not path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        # vẫn cho qua nếu không có phần mở rộng rõ ràng (CDN động), nhưng
        # loại nếu có định dạng lạ không phải ảnh
        if re.search(r"\.(svg|css|js|json)(\?|$)", path):
            return True

    for attr in ("width", "height"):
        val = img_tag.get(attr)
        if val and val.isdigit() and int(val) < 32:
            return True

    css_class = " ".join(img_tag.get("class", [])).lower()
    if any(kw in css_class for kw in IMAGE_URL_BLOCKLIST_KEYWORDS):
        return True

    return False
