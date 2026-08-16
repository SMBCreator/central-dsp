import string
import random
import io
import re
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from typing import Optional
import qrcode
from qrcode.image.pure import PyPNGImage

SHORT_CODE_CHARS = string.ascii_letters + string.digits


def generate_short_code(length: int = 8) -> str:
    return "".join(random.choices(SHORT_CODE_CHARS, k=length))


def generate_qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


# 1x1 transparent GIF bytes
GIF_1X1 = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00"
    b"\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)


MACRO_PATTERN = re.compile(r"\{(sub_id|creative_id|click_id|country|device|platform|timestamp)\}")


def apply_macros(url: str, context: dict) -> str:
    """Replace {macro} placeholders in URL with actual values."""
    def replacer(m):
        key = m.group(1)
        return str(context.get(key, m.group(0)))
    return MACRO_PATTERN.sub(replacer, url)


def add_utm_params(url: str, utm: dict) -> str:
    """Append UTM params to URL without duplicating existing ones."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for k, v in utm.items():
        if k not in qs:
            qs[k] = [v]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse(parsed._replace(query=new_query))
