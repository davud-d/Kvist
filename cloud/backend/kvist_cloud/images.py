# -*- coding: utf-8 -*-
"""Photos that arrive with a message, made safe before anything on the control server touches them.

The danger is not what a picture says (that is the model's business) but what it is: a file crafted for a parser. The
control server holds every customer's secrets, so it never decodes pixels. It reads the *structure* of a PNG or a
JPEG, checks every length and checksum, and writes the picture again from the few parts a picture needs:

* only PNG and JPEG, decided by the bytes, never by the name or the declared type (SVG, HTML, GIF, WebP, HEIC: refused);
* every chunk or segment must be where its length says, with a good checksum; anything after the end is dropped,
  so a polyglot (a picture with a script, an archive or a second file behind it) loses its second life;
* text and metadata are dropped: PNG text chunks, EXIF (GPS, device, a thumbnail of someone else's picture), ICC
  profiles, comments, animation frames; what remains is the image and the few chunks that fix its colours;
* a PNG's compressed pixels are inflated in bounded pieces to the exact size its header promises, so a bomb (a few
  kilobytes that become gigabytes) is refused without ever holding it in memory;
* size, dimensions and counts are capped.

The rewritten bytes go on to the machine, where Kvist's own decoder meets them: a plainer, smaller file than came in.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import zlib
from dataclasses import dataclass

ENVELOPE_TYPE = "kvist-image-goal"
ENVELOPE_PREFIX = '{"type":"kvist-image-goal"'
# The largest message that is looked at at all: eight photos of five megabytes as base64, and some words.
MAX_MESSAGE_CHARS = 28 * 1024 * 1024
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_SIDE = 16384
MAX_PIXELS = 25_000_000
MAX_CHUNKS = 5000
MAX_NAME = 100
# base64 of the biggest allowed file, plus the "data:image/...;base64," head.
MAX_DATA_URL = MAX_IMAGE_BYTES * 4 // 3 + 128
DATA_URL = re.compile(r"data:image/[a-z+.-]{2,20};base64,")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The chunks a picture needs, and the three small ones that fix its colours. Everything else (text, time, EXIF, ICC
# profiles, physical size, animation) is dropped.
PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB"}
PNG_FIXED = {b"gAMA": 4, b"cHRM": 32, b"sRGB": 1}
# colour type -> (channels, allowed bit depths)
PNG_COLOURS = {0: (1, {1, 2, 4, 8, 16}), 2: (3, {8, 16}), 3: (1, {1, 2, 4, 8}), 4: (2, {8, 16}), 6: (4, {8, 16})}


class ImageError(ValueError):
    """The picture is refused; the message says why, in both languages."""


def _bad(english: str, russian: str) -> ImageError:
    return ImageError(f"{english} / {russian}")


# -- PNG ------------------------------------------------------------------------------------------------------


def _expected_png_bytes(width: int, height: int, channels: int, depth: int, interlace: int) -> int:
    """How many bytes the pixels inflate to, filter bytes included: the exact size the header promises."""

    def pass_bytes(w: int, h: int) -> int:
        return 0 if w == 0 or h == 0 else h * (1 + (w * channels * depth + 7) // 8)

    if interlace == 0:
        return pass_bytes(width, height)
    total = 0
    # Adam7: (x start, y start, x step, y step)
    passes = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    for x0, y0, dx, dy in passes:
        total += pass_bytes((width - x0 + dx - 1) // dx, (height - y0 + dy - 1) // dy)
    return total


def _inflate_exactly(compressed: bytes, expected: int) -> None:
    """The zlib stream must end after exactly `expected` bytes. Bounded pieces: a bomb is refused, never held."""
    inflater = zlib.decompressobj()
    produced = 0
    pending = compressed
    try:
        while pending and not inflater.eof:
            piece = inflater.decompress(pending, 1 << 16)
            produced += len(piece)
            if produced > expected:
                raise _bad("The picture expands far beyond its size",
                           "Картинка распаковывается сильно больше своего размера")
            pending = inflater.unconsumed_tail
        if not inflater.eof:
            piece = inflater.flush()
            produced += len(piece)
    except zlib.error as error:
        raise _bad("The picture's data is damaged", "Данные картинки повреждены") from error
    if not inflater.eof or produced != expected or inflater.unused_data:
        raise _bad("The picture's data does not match its header", "Данные картинки не совпадают с заголовком")


def sanitize_png(data: bytes) -> bytes:
    if not data.startswith(PNG_SIGNATURE):
        raise _bad("Not a PNG", "Это не PNG")
    position = len(PNG_SIGNATURE)
    kept: list[tuple[bytes, bytes]] = []
    idat: list[bytes] = []
    header: tuple[int, int, int, int, int] | None = None
    count = 0
    ended = False
    while position < len(data) and not ended:
        count += 1
        if count > MAX_CHUNKS:
            raise _bad("Too many parts in the picture", "Слишком много частей в картинке")
        if position + 8 > len(data):
            raise _bad("The PNG is cut short", "PNG обрезан")
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        end = position + 8 + length + 4
        if length > 0x7FFFFFFF or end > len(data):
            raise _bad("A PNG chunk is longer than the file", "Блок PNG длиннее файла")
        body = data[position + 8:position + 8 + length]
        (checksum,) = struct.unpack(">I", data[end - 4:end])
        if zlib.crc32(kind + body) != checksum:
            raise _bad("A PNG chunk has a bad checksum", "У блока PNG неверная контрольная сумма")
        if header is None and kind != b"IHDR":
            raise _bad("The PNG does not start with its header", "PNG не начинается с заголовка")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise _bad("The PNG header is wrong", "Заголовок PNG неверный")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            if (colour not in PNG_COLOURS or depth not in PNG_COLOURS[colour][1] or compression or filtering
                    or interlace not in (0, 1) or not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE)
                    or width * height > MAX_PIXELS):
                raise _bad("The picture is too big or has an unusual format",
                           "Картинка слишком большая или в необычном формате")
            header = (width, height, depth, colour, interlace)
        if kind == b"IDAT":
            idat.append(body)
        if kind in PNG_FIXED and length != PNG_FIXED[kind]:
            raise _bad("A PNG chunk has a wrong size", "Блок PNG неверного размера")
        if kind == b"PLTE" and (length % 3 or not 3 <= length <= 768):
            raise _bad("The PNG palette is wrong", "Палитра PNG неверная")
        if kind in PNG_KEEP:
            kept.append((kind, body))
        ended = kind == b"IEND"
        position = end
    if header is None or not ended or not idat:
        raise _bad("The PNG is incomplete", "PNG неполный")
    width, height, depth, colour, interlace = header
    if colour == 3 and not any(kind == b"PLTE" for kind, _ in kept):
        raise _bad("The PNG has no palette", "В PNG нет палитры")
    _inflate_exactly(b"".join(idat), _expected_png_bytes(width, height, PNG_COLOURS[colour][0], depth, interlace))
    # Written again from the kept parts, with the pixels in one run of IDAT chunks. Whatever followed IEND is gone.
    out = [PNG_SIGNATURE]
    for kind, body in kept:
        if kind == b"IDAT":
            continue
        if kind == b"IEND":
            out.append(_png_chunk(b"IDAT", b"".join(idat)))
        out.append(_png_chunk(kind, body))
    return b"".join(out)


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


# -- JPEG -----------------------------------------------------------------------------------------------------

JPEG_KEEP = {0xDB, 0xC4, 0xDD, 0xC0, 0xC1, 0xC2, 0xE0, 0xEE}  # tables, restart interval, frame, JFIF, Adobe colour


def sanitize_jpeg(data: bytes) -> bytes:
    if not data.startswith(b"\xff\xd8\xff"):
        raise _bad("Not a JPEG", "Это не JPEG")
    out = [b"\xff\xd8"]
    position = 2
    frame = False
    scans = 0
    segments = 0
    while True:
        segments += 1
        if segments > MAX_CHUNKS:
            raise _bad("Too many parts in the picture", "Слишком много частей в картинке")
        if position + 2 > len(data) or data[position] != 0xFF:
            raise _bad("The JPEG is damaged", "JPEG повреждён")
        marker = data[position + 1]
        if marker == 0xFF:  # fill bytes
            position += 1
            continue
        position += 2
        if marker == 0xD9:  # the end of the picture: whatever follows is not part of it
            break
        if marker == 0x00 or 0xD0 <= marker <= 0xD7 or marker == 0x01 or marker == 0xD8:
            raise _bad("The JPEG is damaged", "JPEG повреждён")
        if position + 2 > len(data):
            raise _bad("The JPEG is cut short", "JPEG обрезан")
        (length,) = struct.unpack(">H", data[position:position + 2])
        if length < 2 or position + length > len(data):
            raise _bad("A JPEG segment is longer than the file", "Сегмент JPEG длиннее файла")
        body = data[position + 2:position + length]
        position += length
        if marker in (0xC0, 0xC1, 0xC2):
            if len(body) < 6 or body[0] != 8:
                raise _bad("The picture has an unusual format", "Картинка в необычном формате")
            height, width = struct.unpack(">HH", body[1:5])
            if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE) or width * height > MAX_PIXELS:
                raise _bad("The picture is too big", "Картинка слишком большая")
            frame = True
        elif marker in (0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise _bad("The picture has an unusual format", "Картинка в необычном формате")
        if marker == 0xDA:  # a scan: its header, then entropy-coded bytes up to the next real marker
            if not frame:
                raise _bad("The JPEG has no frame", "В JPEG нет кадра")
            scans += 1
            start = position
            while True:
                nxt = data.find(b"\xff", position)
                if nxt < 0 or nxt + 1 >= len(data):
                    raise _bad("The JPEG is cut short", "JPEG обрезан")
                follower = data[nxt + 1]
                if follower == 0x00 or 0xD0 <= follower <= 0xD7:
                    position = nxt + 2
                    continue
                if follower == 0xFF:
                    position = nxt + 1
                    continue
                position = nxt
                break
            out.append(b"\xff\xda" + struct.pack(">H", length) + body + data[start:position])
        elif marker in JPEG_KEEP:
            out.append(bytes([0xFF, marker]) + struct.pack(">H", length) + body)
        # every other segment (EXIF, ICC, comments, thumbnails, vendor data) is simply not copied
    if not frame or not scans:
        raise _bad("The JPEG is incomplete", "JPEG неполный")
    out.append(b"\xff\xd9")
    return b"".join(out)


def sanitize_image(data: bytes) -> tuple[bytes, str]:
    """(clean bytes, mime): by what the bytes are. Anything but a whole PNG or JPEG is refused."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise _bad("A photo must be up to 5 MB", "Фото должно быть не больше 5 МБ")
    if data.startswith(PNG_SIGNATURE):
        return sanitize_png(data), "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return sanitize_jpeg(data), "image/jpeg"
    raise _bad("Only PNG and JPEG photos are accepted", "Принимаются только фото PNG и JPEG")


# -- the message that carries them ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prepared:
    forward: str  # what goes to the machine: the whole envelope, pictures rewritten
    stored: str   # what the history keeps: the same envelope without the pixels
    shown: str    # the words, for a title and a list
    count: int


def envelope_of(text: str) -> dict | None:
    """The envelope a text is, by the very rule Kvist's core reads goals by (kernel/vision/vision.py, unpack_goal):
    after leading white space a JSON object whose type is the envelope's. Spaces and key order do not matter to the
    core, so they must not matter here: a text the core would read as pictures is never let through as plain words."""
    if not text.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) and value.get("type") == ENVELOPE_TYPE else None


def _clean_name(raw: object) -> str:
    name = raw if isinstance(raw, str) else "photo"
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable() and char not in "<>`")
    return name.strip()[:MAX_NAME] or "photo"


def prepare(text: str, limit: int) -> Prepared:
    """A message's text made ready: plain text is checked against `limit`; the envelope has every picture rewritten."""
    if len(text) > MAX_MESSAGE_CHARS:
        raise _bad("The message is too long", "Сообщение слишком длинное")
    envelope = envelope_of(text)
    if envelope is None:
        if len(text) > limit:
            raise _bad("The message is too long", "Сообщение слишком длинное")
        return Prepared(text, text, text, 0)
    if set(envelope) - {"type", "text", "images"}:
        raise _bad("The message is damaged", "Сообщение повреждено")
    words = envelope.get("text", "")
    images = envelope.get("images", [])
    if not isinstance(words, str) or len(words) > limit or not isinstance(images, list) or not images \
            or len(images) > MAX_IMAGES:
        raise _bad(f"Up to {MAX_IMAGES} photos per message", f"Не больше {MAX_IMAGES} фото в сообщении")
    sent: list[dict[str, str]] = []
    kept: list[dict[str, str]] = []
    total = 0
    for item in images:
        url = item.get("data_url") if isinstance(item, dict) else None
        if not isinstance(item, dict) or set(item) - {"name", "data_url"} or not isinstance(url, str) \
                or len(url) > MAX_DATA_URL or not DATA_URL.match(url):
            raise _bad("Expected a photo, not a link", "Ожидалось фото, а не ссылка")
        try:
            raw = base64.b64decode(url.partition(",")[2], validate=True)
        except (binascii.Error, ValueError) as error:
            raise _bad("The photo is damaged", "Фото повреждено") from error
        clean, mime = sanitize_image(raw)
        total += len(clean)
        if total > MAX_TOTAL_BYTES:
            raise _bad("The photos are over 20 MB", "Фото больше 20 МБ")
        name = _clean_name(item.get("name"))
        sent.append({"name": name, "data_url": f"data:{mime};base64,{base64.b64encode(clean).decode('ascii')}"})
        kept.append({"name": name, "data_url": ""})
    forward = json.dumps({"type": ENVELOPE_TYPE, "text": words, "images": sent}, separators=(",", ":"))
    stored = json.dumps({"type": ENVELOPE_TYPE, "text": words, "images": kept}, separators=(",", ":"))
    return Prepared(forward, stored, words.strip(), len(sent))
