"""
School Book Sticker Generator
=============================
Generates print-ready ID stickers (photo + student details) for school
textbooks, laid out precisely on a 13x19 inch sheet at 300 DPI.

Built with:
  - Gradio        -> web UI
  - Pillow (PIL)  -> high-resolution raster generation
  - reportlab     -> optional vector-accurate PDF export (true mm/inch sizing)

Run:
    pip install gradio pillow reportlab --break-system-packages
    python app.py
"""

import io
import os
import math
import textwrap
from dataclasses import dataclass

import gradio as gr
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

# reportlab gives us a true vector PDF at exact physical dimensions.
# It's optional -- the PNG sheet works fully without it.
try:
    from reportlab.lib.units import mm, inch
    from reportlab.pdfgen import canvas as pdf_canvas
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False


# ============================================================
# PRECISE PHYSICAL MEASUREMENTS (computed, not guessed)
# ============================================================

DPI = 300

MM_PER_INCH = 25.4


def mm_to_px(value_mm: float, dpi: int = DPI) -> int:
    return round((value_mm / MM_PER_INCH) * dpi)


def inch_to_px(value_in: float, dpi: int = DPI) -> int:
    return round(value_in * dpi)


# Sticker: 9.2 cm x 5.4 cm  ->  92mm x 54mm
STICKER_W_MM = 92.0
STICKER_H_MM = 54.0
STICKER_W = mm_to_px(STICKER_W_MM)
STICKER_H = mm_to_px(STICKER_H_MM)

# Sheet: 13 x 19 inch
SHEET_W_IN = 13.0
SHEET_H_IN = 19.0
SHEET_W = inch_to_px(SHEET_W_IN)
SHEET_H = inch_to_px(SHEET_H_IN)

# Print-safe margins / gaps (also computed from real units, not magic numbers)
PAGE_MARGIN_MM = 6.0     # outer margin on the sheet
STICKER_GAP_MM = 2.0     # gap between stickers (cut allowance)

PAGE_MARGIN = mm_to_px(PAGE_MARGIN_MM)
STICKER_GAP = mm_to_px(STICKER_GAP_MM)

# Internal sticker padding / supersampling for crisp text & circle edges
SS = 2  # supersample factor while drawing the sticker, then downscale


# ============================================================
# FONT LOADING (robust fallback chain)
# ============================================================

FONT_CANDIDATES_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttf",
    "arial.ttf",
]
FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\segoeuib.ttf",
    "arialbd.ttf",
]


def _load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def font_regular(size):
    return _load_font(FONT_CANDIDATES_REGULAR, size)


def font_bold(size):
    return _load_font(FONT_CANDIDATES_BOLD, size)


# ============================================================
# THEME (color presets the user can pick in the UI)
# ============================================================

THEMES = {
    "Royal Blue":  {"primary": (16, 78, 161),  "accent": (235, 244, 255), "text": (25, 25, 25)},
    "Crimson Red": {"primary": (178, 34, 52),  "accent": (255, 238, 239), "text": (25, 25, 25)},
    "Forest Green":{"primary": (27, 110, 71),  "accent": (232, 247, 238), "text": (25, 25, 25)},
    "Sunset Orange":{"primary": (214, 109, 13),"accent": (255, 244, 230), "text": (25, 25, 25)},
    "Slate Gray":  {"primary": (55, 65, 81),   "accent": (240, 241, 243), "text": (25, 25, 25)},
    "Royal Purple":{"primary": (95, 39, 165),  "accent": (244, 237, 255), "text": (25, 25, 25)},
}

# Card backdrop styles (drawn behind/around the white content card)
BACKGROUND_STYLES = [
    "Plain White",
    "Soft Tint",
    "Diagonal Stripes",
    "Dot Grid",
    "Gradient Wash",
]

# Photo color treatments
PHOTO_FILTERS = [
    "None",
    "Grayscale",
    "Sepia",
    "High Contrast B&W",
    "Cool Tone",
    "Warm Tone",
]

INSPIRATIONAL_QUOTES = [
    "None",
    "Dream big. Work hard. Stay focused.",
    "Believe you can, and you're halfway there.",
    "Today's effort is tomorrow's success.",
    "Knowledge is power, curiosity is the key.",
    "Every expert was once a beginner.",
    "Aim for the stars, work for the ground.",
    "Small steps every day lead to big dreams.",
    "Education is the passport to the future.",
    "Stay curious. Stay kind. Stay determined.",
    "Your only limit is the one you set yourself.",
    "Custom...",
]


# ============================================================
# DRAWING HELPERS
# ============================================================

def draw_text_fit(draw, xy, text, max_width, font_loader, start_size, min_size, fill, bold=False, anchor=None):
    """Shrinks font size until the text fits within max_width, never wraps mid-word."""
    size = start_size
    loader = font_bold if bold else font_regular
    font = loader(size)
    while size > min_size and draw.textlength(text, font=font) > max_width:
        size -= 1
        font = loader(size)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)
    return font


def fit_label_value(draw, x, y, label, value, label_w, font_label, font_value, fill, max_width):
    """Draws 'Label : Value' with aligned colons and auto-shrinking value text."""
    draw.text((x, y), label, font=font_label, fill=fill)
    colon_x = x + label_w
    draw.text((colon_x, y), ":", font=font_label, fill=fill)
    val_x = colon_x + draw.textlength("  ", font=font_label) + 6

    avail = max_width - (val_x - x)
    size = font_value.size
    f = font_value
    text = value if value else "-"
    while size > 14 and draw.textlength(text, font=f) > avail:
        size -= 1
        f = font_regular(size)
    draw.text((val_x, y), text, font=f, fill=fill)


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def circular_mask(size):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size[0] - 1, size[1] - 1], fill=255)
    return mask


def apply_photo_filter(photo: Image.Image, filter_name: str) -> Image.Image:
    """Applies a color treatment to the student photo."""
    if filter_name == "None" or not filter_name:
        return photo

    if filter_name == "Grayscale":
        return ImageOps.grayscale(photo).convert("RGB")

    if filter_name == "High Contrast B&W":
        gray = ImageOps.grayscale(photo)
        gray = ImageOps.autocontrast(gray, cutoff=2)
        return gray.convert("RGB")

    if filter_name == "Sepia":
        gray = ImageOps.grayscale(photo)
        sepia = ImageOps.colorize(gray, black=(40, 26, 13), white=(255, 240, 192))
        return sepia.convert("RGB")

    if filter_name == "Cool Tone":
        r, g, b = photo.split()
        b = b.point(lambda i: min(255, int(i * 1.12)))
        r = r.point(lambda i: int(i * 0.94))
        return Image.merge("RGB", (r, g, b))

    if filter_name == "Warm Tone":
        r, g, b = photo.split()
        r = r.point(lambda i: min(255, int(i * 1.10)))
        b = b.point(lambda i: int(i * 0.90))
        return Image.merge("RGB", (r, g, b))

    return photo


def draw_card_background(W, H, style, accent, primary):
    """Returns an RGB image (W x H) used as the sticker backdrop, drawn behind
    the white content card. Patterns are intentionally subtle so they never
    fight with the printed text."""
    bg = Image.new("RGB", (W, H), "white")

    if style in (None, "Plain White"):
        return bg

    if style == "Soft Tint":
        bg = Image.new("RGB", (W, H), accent)
        return bg

    if style == "Gradient Wash":
        top = accent
        bottom = (255, 255, 255)
        for y in range(H):
            t = y / max(1, H - 1)
            r = int(top[0] + (bottom[0] - top[0]) * t)
            g = int(top[1] + (bottom[1] - top[1]) * t)
            b = int(top[2] + (bottom[2] - top[2]) * t)
            ImageDraw.Draw(bg).line([(0, y), (W, y)], fill=(r, g, b))
        return bg

    if style == "Diagonal Stripes":
        d = ImageDraw.Draw(bg)
        stripe_w = max(6, int(H * 0.05))
        step = stripe_w * 2
        # draw stripes across a wide diagonal band so corners are covered
        offset = -H
        x = offset
        while x < W + H:
            d.polygon(
                [(x, 0), (x + stripe_w, 0), (x + stripe_w - H, H), (x - H, H)],
                fill=accent,
            )
            x += step
        return bg

    if style == "Dot Grid":
        d = ImageDraw.Draw(bg)
        spacing = max(18, int(H * 0.09))
        radius = max(2, int(spacing * 0.09))
        for yy in range(spacing // 2, H, spacing):
            for xx in range(spacing // 2, W, spacing):
                d.ellipse([xx - radius, yy - radius, xx + radius, yy + radius], fill=accent)
        return bg

    return bg


# ============================================================
# SINGLE STICKER RENDERING
# ============================================================

@dataclass
class StudentInfo:
    photo_path: str
    name: str
    subject: str
    student_class: str
    roll_no: str
    address: str
    phone: str
    school_name: str
    theme: str
    photo_shape: str  # "Circle" or "Rounded Square"
    background_style: str = "Plain White"
    photo_filter: str = "None"
    photo_zoom: float = 1.0       # 1.0 = fit exactly, >1.0 = zoom in (crop tighter)
    photo_offset_x: float = 0.0   # -1.0..1.0, fraction of free pan range
    photo_offset_y: float = 0.0   # -1.0..1.0
    caption: str = ""             # inspirational line printed on the sticker


def create_sticker(info: StudentInfo) -> Image.Image:
    theme = THEMES.get(info.theme, THEMES["Royal Blue"])
    primary = theme["primary"]
    accent = theme["accent"]
    text_color = theme["text"]

    # Supersample for crisper edges/text, downscale at the end
    W, H = STICKER_W * SS, STICKER_H * SS

    sticker = draw_card_background(W, H, info.background_style, accent, primary)
    draw = ImageDraw.Draw(sticker)

    border_radius = int(0.05 * H)

    # ---- background panel + border ----
    # A solid white inset panel keeps text legible even over patterned/gradient
    # backdrops, while leaving a visible margin of the background style around it.
    has_bg_style = info.background_style not in (None, "Plain White")
    inset = int(0.05 * H) if has_bg_style else 0
    panel_box = [inset, inset, W - 1 - inset, H - 1 - inset]
    draw.rounded_rectangle(panel_box, radius=border_radius, fill="white",
                            outline=primary, width=max(2, int(0.012 * H)))

    # ---- header bar ----
    header_h = int(H * 0.17) + inset
    header_path = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(header_path)
    hd.rounded_rectangle([inset, inset, W - 1 - inset, header_h + border_radius], radius=border_radius, fill=primary)
    hd.rectangle([inset, border_radius + inset, W - 1 - inset, header_h], fill=primary)
    sticker.paste(header_path, (0, 0), header_path)

    school_font = font_bold(int(header_h * 0.42))
    school_text = info.school_name.upper() if info.school_name else "SCHOOL NAME"
    # auto-shrink school name to fit header width
    sf_size = school_font.size
    while sf_size > 14 and draw.textlength(school_text, font=school_font) > W - int(0.06 * W) - 2 * inset:
        sf_size -= 1
        school_font = font_bold(sf_size)
    draw.text((int(0.04 * W) + inset, header_h * 0.5), school_text, font=school_font,
              fill="white", anchor="lm")

    sub_font = font_regular(int(header_h * 0.22))
    draw.text((int(0.04 * W) + inset, header_h * 0.85), "STUDENT IDENTIFICATION STICKER",
              font=sub_font, fill=(255, 255, 255), anchor="lm")

    # ---- footer divider (reserve space first so nothing overlaps it) ----
    footer_y = H - int(H * 0.075)

    caption_text = (info.caption or "").strip()
    caption_h = int(H * 0.075) if caption_text else 0
    content_bottom = footer_y - int(H * 0.02) - caption_h  # hard floor for detail/name text

    # ---- photo placeholder / photo ----
    # Photo + name must both fit above content_bottom, so size the photo from
    # the space actually available rather than a fixed fraction of H.
    photo_y = header_h + int(0.08 * H)
    name_block_h = int(H * 0.16)  # reserved for name text + its top margin
    photo_box = max(int(H * 0.30), min(int(H * 0.56), content_bottom - name_block_h - photo_y))
    photo_x = int(0.045 * W)

    photo_frame_pad = max(2, int(0.01 * H))
    if info.photo_shape == "Circle":
        mask_fn = circular_mask
        frame_draw = lambda d, box: d.ellipse(box, outline=primary, width=max(3, int(0.015 * H)))
    else:
        mask_fn = lambda size: rounded_mask(size, int(0.12 * size[0]))
        frame_draw = lambda d, box: d.rounded_rectangle(box, radius=int(0.12 * photo_box),
                                                          outline=primary, width=max(3, int(0.015 * H)))

    photo_placed = False
    if info.photo_path and os.path.exists(info.photo_path):
        try:
            photo = Image.open(info.photo_path).convert("RGB")
            photo = ImageOps.exif_transpose(photo)
            photo = apply_photo_filter(photo, info.photo_filter)

            # --- adjustable crop: zoom (>=1.0) + pan offset within the free range ---
            zoom = max(1.0, min(3.0, float(info.photo_zoom or 1.0)))
            src_w, src_h = photo.size
            target_ratio = 1.0  # square photo slot
            # first, crop the source to the target aspect ratio at full size
            if src_w / src_h > target_ratio:
                base_h = src_h
                base_w = int(base_h * target_ratio)
            else:
                base_w = src_w
                base_h = int(base_w / target_ratio)
            # now shrink the crop window by the zoom factor (zoom in = smaller window)
            crop_w = max(1, int(base_w / zoom))
            crop_h = max(1, int(base_h / zoom))

            max_off_x = (src_w - crop_w) / 2
            max_off_y = (src_h - crop_h) / 2
            off_x = float(info.photo_offset_x or 0.0) * max_off_x
            off_y = float(info.photo_offset_y or 0.0) * max_off_y

            cx = src_w / 2 + off_x
            cy = src_h / 2 + off_y
            left = int(max(0, min(src_w - crop_w, cx - crop_w / 2)))
            top = int(max(0, min(src_h - crop_h, cy - crop_h / 2)))
            cropped = photo.crop((left, top, left + crop_w, top + crop_h))

            photo = cropped.resize((photo_box, photo_box), Image.LANCZOS)
            mask = mask_fn((photo_box, photo_box))
            sticker.paste(photo, (photo_x, photo_y), mask)
            photo_placed = True
        except Exception:
            photo_placed = False

    if not photo_placed:
        ph_bg = Image.new("RGB", (photo_box, photo_box), accent)
        mask = mask_fn((photo_box, photo_box))
        sticker.paste(ph_bg, (photo_x, photo_y), mask)
        ph_font = font_regular(int(photo_box * 0.13))
        pdraw = ImageDraw.Draw(sticker)
        pdraw.text((photo_x + photo_box / 2, photo_y + photo_box / 2 - photo_box * 0.06),
                    "ADD", font=ph_font, fill=primary, anchor="mm")
        pdraw.text((photo_x + photo_box / 2, photo_y + photo_box / 2 + photo_box * 0.08),
                    "PHOTO", font=ph_font, fill=primary, anchor="mm")

    frame_draw(draw, [photo_x, photo_y, photo_x + photo_box, photo_y + photo_box])

    # ---- student name (bold, prominent, under photo) ----
    name_font = font_bold(int(H * 0.078))
    name_text = (info.name or "Student Name").strip()
    name_y = photo_y + photo_box + int(H * 0.035)
    nf_size = name_font.size
    while nf_size > 12 and draw.textlength(name_text, font=name_font) > photo_box:
        nf_size -= 1
        name_font = font_bold(nf_size)
    # clamp so the name never runs past the card's content floor
    name_bbox = draw.textbbox((0, 0), name_text, font=name_font)
    name_h = name_bbox[3] - name_bbox[1]
    if name_y + name_h > content_bottom:
        name_y = content_bottom - name_h
    draw.text((photo_x + photo_box / 2, name_y), name_text, font=name_font,
              fill=primary, anchor="ma")

    # ---- detail block (right side of photo) ----
    detail_x = photo_x + photo_box + int(0.05 * W)
    detail_w = W - detail_x - int(0.04 * W)
    detail_y = header_h + int(0.09 * H)

    # Budget the vertical space precisely: 4 detail rows + address label + up to
    # 2 address lines, all must fit between detail_y and content_bottom.
    available_h = content_bottom - detail_y
    n_detail_rows = 4
    n_address_lines = 2
    n_total_lines = n_detail_rows + 1 + n_address_lines  # +1 for "Address:" label row
    line_h = available_h / n_total_lines

    detail_font_size = max(14, int(min(H * 0.072, line_h * 0.66)))
    label_font = font_bold(detail_font_size)
    value_font = font_regular(detail_font_size)
    label_w = max(
        draw.textlength("Subject", font=label_font),
        draw.textlength("Class", font=label_font),
        draw.textlength("Roll No", font=label_font),
        draw.textlength("Phone", font=label_font),
    )

    rows = [
        ("Subject", info.subject),
        ("Class", info.student_class),
        ("Roll No", info.roll_no),
        ("Phone", info.phone),
    ]
    y = detail_y
    for label, value in rows:
        fit_label_value(draw, detail_x, y, label, value, label_w, label_font, value_font,
                         text_color, detail_w)
        y += line_h

    # ---- address (wrapped, fits remaining budget exactly) ----
    addr_font_size = max(12, int(detail_font_size * 0.88))
    addr_label_font = font_bold(addr_font_size)
    addr_font = font_regular(addr_font_size)
    draw.text((detail_x, y), "Address", font=addr_label_font, fill=text_color)
    draw.text((detail_x + label_w, y), ":", font=addr_label_font, fill=text_color)
    y_addr = y + line_h

    address_text = (info.address or "-").strip()
    # wrap based on actual pixel width, not a fixed character guess
    words = address_text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=addr_font) <= detail_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    addr_line_h = max(line_h, addr_font_size * 1.05)
    for i in range(n_address_lines):
        if i >= len(lines):
            break
        line = lines[i]
        is_last_visible = (i == n_address_lines - 1)
        if is_last_visible and len(lines) > n_address_lines:
            while draw.textlength(line + "...", font=addr_font) > detail_w and len(line) > 1:
                line = line[:-1]
            line = line + "..."
        draw.text((detail_x, y_addr), line, font=addr_font, fill=text_color)
        y_addr += addr_line_h

    # ---- inspirational caption strip (optional) ----
    if caption_text:
        cap_font = font_bold(int(caption_h * 0.46))
        cap_size = cap_font.size
        avail_cap_w = W - int(0.12 * W)
        while cap_size > 12 and draw.textlength(f"\u201C{caption_text}\u201D", font=cap_font) > avail_cap_w:
            cap_size -= 1
            cap_font = font_bold(cap_size)
        cap_y = footer_y - caption_h / 2 + int(0.01 * H)
        draw.text((W / 2, cap_y), f"\u201C{caption_text}\u201D", font=cap_font,
                  fill=primary, anchor="mm")

    # ---- footer divider line ----
    draw.line([(int(0.045 * W), footer_y), (W - int(0.045 * W), footer_y)],
               fill=accent, width=max(2, int(0.006 * H)))

    sticker = sticker.resize((STICKER_W, STICKER_H), Image.LANCZOS)
    return sticker


# ============================================================
# SHEET LAYOUT (auto-centered grid + optional crop marks)
# ============================================================

def compute_grid():
    avail_w = SHEET_W - 2 * PAGE_MARGIN
    avail_h = SHEET_H - 2 * PAGE_MARGIN
    cols = max(1, (avail_w + STICKER_GAP) // (STICKER_W + STICKER_GAP))
    rows = max(1, (avail_h + STICKER_GAP) // (STICKER_H + STICKER_GAP))
    grid_w = cols * STICKER_W + (cols - 1) * STICKER_GAP
    grid_h = rows * STICKER_H + (rows - 1) * STICKER_GAP
    offset_x = (SHEET_W - grid_w) // 2
    offset_y = (SHEET_H - grid_h) // 2
    return int(cols), int(rows), int(offset_x), int(offset_y)


def draw_crop_marks(draw, x, y, w, h, length=14, color=(160, 160, 160)):
    pts = [
        ((x, y), (x - length, y), (x, y - length)),
        ((x + w, y), (x + w + length, y), (x + w, y - length)),
        ((x, y + h), (x - length, y + h), (x, y + h + length)),
        ((x + w, y + h), (x + w + length, y + h), (x + w, y + h + length)),
    ]
    for corner, h_end, v_end in pts:
        draw.line([corner, h_end], fill=color, width=2)
        draw.line([corner, v_end], fill=color, width=2)


def generate_sheet(info: StudentInfo, show_crop_marks: bool, copies: int):
    sticker = create_sticker(info)
    cols, rows, off_x, off_y = compute_grid()

    sheet = Image.new("RGB", (SHEET_W, SHEET_H), "white")
    sdraw = ImageDraw.Draw(sheet)

    total_slots = cols * rows
    placed = 0
    for r in range(rows):
        for c in range(cols):
            if placed >= min(copies, total_slots):
                break
            x = off_x + c * (STICKER_W + STICKER_GAP)
            y = off_y + r * (STICKER_H + STICKER_GAP)
            sheet.paste(sticker, (x, y))
            if show_crop_marks:
                draw_crop_marks(sdraw, x, y, STICKER_W, STICKER_H)
            placed += 1
        if placed >= min(copies, total_slots):
            break

    return sheet, cols, rows, total_slots


def save_png(sheet: Image.Image, path: str):
    sheet.save(path, dpi=(DPI, DPI))


def save_pdf(sheet: Image.Image, path: str):
    """True-size vector PDF: page is exactly 13x19in, image embedded at 300 DPI."""
    if not REPORTLAB_OK:
        return None
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", dpi=(DPI, DPI))
    buf.seek(0)
    from reportlab.lib.utils import ImageReader
    c = pdf_canvas.Canvas(path, pagesize=(SHEET_W_IN * inch, SHEET_H_IN * inch))
    c.drawImage(ImageReader(buf), 0, 0, width=SHEET_W_IN * inch, height=SHEET_H_IN * inch)
    c.showPage()
    c.save()
    return path


# ============================================================
# GRADIO CALLBACKS
# ============================================================

import tempfile

OUT_DIR = os.path.join(tempfile.gettempdir(), "sticker_output")
os.makedirs(OUT_DIR, exist_ok=True)


def build_info(photo, school_name, name, subject, student_class, roll_no, address, phone, theme, photo_shape,
                background_style, photo_filter, photo_zoom, photo_offset_x, photo_offset_y, caption_choice, caption_custom):
    caption = caption_custom if caption_choice == "Custom..." else caption_choice
    if caption == "None":
        caption = ""
    return StudentInfo(
        photo_path=photo,
        name=name or "",
        subject=subject or "",
        student_class=student_class or "",
        roll_no=roll_no or "",
        address=address or "",
        phone=phone or "",
        school_name=school_name or "",
        theme=theme,
        photo_shape=photo_shape,
        background_style=background_style,
        photo_filter=photo_filter,
        photo_zoom=photo_zoom,
        photo_offset_x=photo_offset_x,
        photo_offset_y=photo_offset_y,
        caption=caption or "",
    )


def preview_sticker(photo, school_name, name, subject, student_class, roll_no, address, phone, theme, photo_shape,
                     background_style, photo_filter, photo_zoom, photo_offset_x, photo_offset_y,
                     caption_choice, caption_custom):
    info = build_info(photo, school_name, name, subject, student_class, roll_no, address, phone, theme, photo_shape,
                       background_style, photo_filter, photo_zoom, photo_offset_x, photo_offset_y,
                       caption_choice, caption_custom)
    return create_sticker(info)


def do_generate(photo, school_name, name, subject, student_class, roll_no, address, phone,
                 theme, photo_shape, background_style, photo_filter, photo_zoom, photo_offset_x, photo_offset_y,
                 caption_choice, caption_custom, show_crop_marks, copies, export_pdf):
    info = build_info(photo, school_name, name, subject, student_class, roll_no, address, phone, theme, photo_shape,
                       background_style, photo_filter, photo_zoom, photo_offset_x, photo_offset_y,
                       caption_choice, caption_custom)
    copies = int(copies) if copies else 24
    sheet, cols, rows, total = generate_sheet(info, show_crop_marks, copies)

    png_path = os.path.join(OUT_DIR, "school_sticker_sheet.png")
    save_png(sheet, png_path)

    files = [png_path]
    if export_pdf and REPORTLAB_OK:
        pdf_path = os.path.join(OUT_DIR, "school_sticker_sheet.pdf")
        save_pdf(sheet, pdf_path)
        files.append(pdf_path)

    status = (
        f"✅ Sheet generated: {cols} columns × {rows} rows = {total} stickers/sheet "
        f"(placed {min(copies, total)}).\n"
        f"Sticker: {STICKER_W_MM:.1f}mm × {STICKER_H_MM:.1f}mm  "
        f"({STICKER_W}×{STICKER_H}px @ {DPI} DPI)\n"
        f"Sheet: {SHEET_W_IN}\" × {SHEET_H_IN}\"  ({SHEET_W}×{SHEET_H}px @ {DPI} DPI)"
    )
    return sheet, files, status


def grid_info_text():
    cols, rows, _, _ = compute_grid()
    return (
        f"**Layout:** {cols} columns × {rows} rows = **{cols * rows} stickers per sheet**  \n"
        f"Sticker: {STICKER_W_MM:.1f} × {STICKER_H_MM:.1f} mm &nbsp;|&nbsp; "
        f"Sheet: {SHEET_W_IN}\" × {SHEET_H_IN}\" &nbsp;|&nbsp; {DPI} DPI"
    )


# ============================================================
# UI
# ============================================================

CUSTOM_CSS = """
.gradio-container {max-width: 1280px !important; margin: auto;}
#title_block {text-align:center; margin-bottom: 0.5rem;}
#status_box textarea {font-family: ui-monospace, monospace; font-size: 0.85rem;}
footer {visibility: hidden}
"""

with gr.Blocks(title="School Book Sticker Generator", css=CUSTOM_CSS, theme=gr.themes.Soft(primary_hue="blue")) as app:

    gr.Markdown(
        f"""
        <div id="title_block">

        # 🏷️ School Book Sticker Generator

        Create print-ready student ID stickers with photo, name, subject, class, roll number,
        address and phone — with adjustable photo zoom/pan, color filters, background styles,
        and an optional inspirational caption — automatically arranged on a
        **13×19&Prime; sheet at 300 DPI PNG**.

        </div>
        """
    )

    with gr.Row():
        # -------------------- LEFT: INPUT FORM --------------------
        with gr.Column(scale=4):
            gr.Markdown("### 1. Student Details")

            photo = gr.Image(type="filepath", label="Student Photo", height=220)

            with gr.Row():
                photo_shape = gr.Radio(["Circle", "Rounded Square"], value="Circle", label="Photo Shape")
                theme = gr.Dropdown(list(THEMES.keys()), value="Royal Blue", label="Color Theme")

            with gr.Accordion("📸 Photo Adjustment & Filters", open=False):
                photo_filter = gr.Dropdown(PHOTO_FILTERS, value="None", label="Photo Filter")
                photo_zoom = gr.Slider(1.0, 3.0, value=1.0, step=0.05, label="Zoom (crop tighter)")
                with gr.Row():
                    photo_offset_x = gr.Slider(-1.0, 1.0, value=0.0, step=0.05, label="Pan Left / Right")
                    photo_offset_y = gr.Slider(-1.0, 1.0, value=0.0, step=0.05, label="Pan Up / Down")

            background_style = gr.Dropdown(BACKGROUND_STYLES, value="Plain White", label="Sticker Background Style")

            school_name = gr.Textbox(label="School Name", placeholder="e.g. India Mission High School",
                                      value="India Mission High School")
            name = gr.Textbox(label="Student Name", placeholder="e.g. Aarav Sharma")

            with gr.Row():
                subject = gr.Textbox(label="Subject", placeholder="e.g. Mathematics")
                student_class = gr.Textbox(label="Class", placeholder="e.g. 8-B")

            with gr.Row():
                roll_no = gr.Textbox(label="Roll Number", placeholder="e.g. 23")
                phone = gr.Textbox(label="Phone Number", placeholder="e.g. 98765 43210")

            address = gr.Textbox(label="Address", lines=2, placeholder="House no, Street, City, Pin")

            gr.Markdown("### 2. Inspirational Caption (optional)")
            with gr.Row():
                caption_choice = gr.Dropdown(INSPIRATIONAL_QUOTES, value="None", label="Caption")
                caption_custom = gr.Textbox(label="Custom caption text", placeholder="Only used if 'Custom...' is selected")

            gr.Markdown("### 3. Sheet Options")
            with gr.Row():
                copies = gr.Slider(1, 24, value=24, step=1, label="Number of stickers on sheet")
                show_crop_marks = gr.Checkbox(value=True, label="Show crop marks (cutting guides)")
            export_pdf = gr.Checkbox(value=REPORTLAB_OK, interactive=REPORTLAB_OK,
                                      label="Also export true-size PDF" + ("" if REPORTLAB_OK else " (install reportlab to enable)"))

            gr.Markdown(grid_info_text())

            with gr.Row():
                preview_btn = gr.Button("🔍 Preview Sticker", variant="secondary")
                generate_btn = gr.Button("🖨️ Generate Print Sheet", variant="primary")

        # -------------------- RIGHT: OUTPUT --------------------
        with gr.Column(scale=5):
            gr.Markdown("### Preview")
            sticker_preview = gr.Image(label="Single Sticker Preview", height=320)

            gr.Markdown("### Print Sheet")
            sheet_preview = gr.Image(label="Full Sheet (13×19in @ 300 DPI)", height=520)
            sheet_files = gr.Files(label="Download Files")
            status_box = gr.Textbox(label="Status", lines=4, interactive=False, elem_id="status_box")

    inputs = [photo, school_name, name, subject, student_class, roll_no, address, phone, theme, photo_shape,
              background_style, photo_filter, photo_zoom, photo_offset_x, photo_offset_y,
              caption_choice, caption_custom]

    preview_btn.click(fn=preview_sticker, inputs=inputs, outputs=sticker_preview)

    generate_btn.click(
        fn=do_generate,
        inputs=inputs + [show_crop_marks, copies, export_pdf],
        outputs=[sheet_preview, sheet_files, status_box],
    )

    # live preview as fields change (debounced by Gradio's default behavior)
    for comp in inputs:
        comp.change(fn=preview_sticker, inputs=inputs, outputs=sticker_preview)


if __name__ == "__main__":
    app.launch(allowed_paths=[OUT_DIR])