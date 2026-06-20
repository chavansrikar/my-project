from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

# ==========================================
# SETTINGS
# ==========================================

LOGO_FILE = "IMG-20250709-WA0001.jpg (13)
.jpeg"   # Rename your image to logo.jpeg

OUTPUT_PNG = "credit_card_sheet.png"
OUTPUT_PDF = "credit_card_sheet.pdf"

# Sheet Size
SHEET_W_IN = 13
SHEET_H_IN = 19

# Standard Credit Card Size
CARD_WIDTH_IN = 3.375
CARD_HEIGHT_IN = 2.125

# Gap Between Cards
GAP_IN = 0.05

# Print Quality
DPI = 300

# ==========================================
# CALCULATIONS
# ==========================================

sheet_w_px = int(SHEET_W_IN * DPI)
sheet_h_px = int(SHEET_H_IN * DPI)

card_w_px = int(CARD_WIDTH_IN * DPI)
card_h_px = int(CARD_HEIGHT_IN * DPI)

gap_px = int(GAP_IN * DPI)

# Create sheet
sheet = Image.new("RGB", (sheet_w_px, sheet_h_px), "white")
draw = ImageDraw.Draw(sheet)

# ==========================================
# LOAD LOGO
# ==========================================

logo = Image.open(LOGO_FILE).convert("RGBA")

# ==========================================
# CREATE CARD TEMPLATE
# ==========================================

card = Image.new(
    "RGBA",
    (card_w_px, card_h_px),
    (255, 255, 255, 255)
)

# Small margin
margin = 20

logo.thumbnail(
    (
        card_w_px - margin * 2,
        card_h_px - margin * 2
    ),
    Image.LANCZOS
)

logo_x = (card_w_px - logo.width) // 2
logo_y = (card_h_px - logo.height) // 2

card.paste(
    logo,
    (logo_x, logo_y),
    logo
)

# ==========================================
# MAXIMUM FIT
# ==========================================

cols = (sheet_w_px + gap_px) // (card_w_px + gap_px)
rows = (sheet_h_px + gap_px) // (card_h_px + gap_px)

grid_width = cols * card_w_px + (cols - 1) * gap_px
grid_height = rows * card_h_px + (rows - 1) * gap_px

start_x = (sheet_w_px - grid_width) // 2
start_y = (sheet_h_px - grid_height) // 2

print("=" * 50)
print("13 x 19 Credit Card Sheet Generator")
print("=" * 50)
print(f"Card Size : {CARD_WIDTH_IN}\" x {CARD_HEIGHT_IN}\"")
print(f"Columns   : {cols}")
print(f"Rows      : {rows}")
print(f"Total     : {cols * rows}")
print("=" * 50)

# ==========================================
# PLACE CARDS
# ==========================================

for r in range(rows):
    for c in range(cols):

        x = start_x + c * (card_w_px + gap_px)
        y = start_y + r * (card_h_px + gap_px)

        sheet.paste(card, (x, y), card)

        # Cut guide
        draw.rectangle(
            (
                x,
                y,
                x + card_w_px,
                y + card_h_px
            ),
            outline=(180, 180, 180),
            width=2
        )

# ==========================================
# SAVE PNG
# ==========================================

sheet.save(
    OUTPUT_PNG,
    dpi=(DPI, DPI)
)

# ==========================================
# SAVE PDF
# ==========================================

pdf = canvas.Canvas(
    OUTPUT_PDF,
    pagesize=(13 * inch, 19 * inch)
)

pdf.drawImage(
    OUTPUT_PNG,
    0,
    0,
    width=13 * inch,
    height=19 * inch
)

pdf.save()

print("SUCCESS!")
print(f"PNG Saved : {OUTPUT_PNG}")
print(f"PDF Saved : {OUTPUT_PDF}")
print("Ready for Printing.")