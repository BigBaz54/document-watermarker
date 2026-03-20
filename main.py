import io
import logging
import math
import os
from datetime import date
from pathlib import Path

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "Copie destinée à : — Usage : — Date : {date}")
RASTERIZE_DPI = int(os.getenv("RASTERIZE_DPI", "300"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
WATERMARK_OPACITY = int(os.getenv("WATERMARK_OPACITY", "110"))
WATERMARK_ROWS = int(os.getenv("WATERMARK_ROWS", "8"))

MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
A4_RATIO = 1.4142
WATERMARK_COLORS = [
    (0, 0, 180),
    (180, 0, 0),
    (0, 0, 0),
    (128, 128, 128),
]

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Document Watermarker")


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if os.path.exists(FONT_PATH):
        return ImageFont.truetype(FONT_PATH, size)
    return ImageFont.load_default(size)


def create_watermark_overlay(width: int, height: int, text: str) -> Image.Image:
    """Create a transparent overlay with diagonal tiled watermark text."""
    # Normalize: if landscape, compute as if placed in a portrait A4 page
    ref = int(width * A4_RATIO) if width > height else height

    num_rows = WATERMARK_ROWS
    step_y = ref // num_rows
    font_size = max(12, step_y // 6)
    font = _load_font(font_size)

    # Measure text
    bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]

    colors = [(*c, WATERMARK_OPACITY) for c in WATERMARK_COLORS]
    step_x = int(text_w * 1.1)
    wave_amplitude = step_y // 8
    row_shift = step_x // num_rows

    # Draw text flat on an oversized canvas, then rotate once
    diag = int(math.sqrt(width**2 + height**2))
    canvas = Image.new("RGBA", (diag * 2, diag * 2), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(canvas)

    total_rows = diag * 2 // step_y + 1
    for row in range(total_rows):
        color = colors[row % len(colors)]
        cy = row * step_y
        wavy = row % 3 == 1
        x = (row % num_rows) * row_shift
        col = 0
        while x < diag * 2:
            dy = int(math.sin(col * 2 * math.pi / 6) * wave_amplitude) if wavy else 0
            cdraw.text((x, cy + dy), text, font=font, fill=color)
            x += step_x
            col += 1

    canvas = canvas.rotate(30, resample=Image.BICUBIC, expand=False)
    cx, cy = canvas.width // 2, canvas.height // 2
    return canvas.crop((cx - width // 2, cy - height // 2,
                        cx - width // 2 + width, cy - height // 2 + height))


def apply_watermark(image: Image.Image, text: str) -> Image.Image:
    """Apply watermark overlay to a PIL Image."""
    image.load()
    image = image.convert("RGB").convert("RGBA")
    overlay = create_watermark_overlay(image.width, image.height, text)
    return Image.alpha_composite(image, overlay)


def process_image(data: bytes, ext: str, text: str) -> io.BytesIO:
    """Watermark a single image, return bytes in the same format."""
    watermarked = apply_watermark(Image.open(io.BytesIO(data)), text)
    output = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        watermarked.convert("RGB").save(output, format="JPEG", quality=95)
    elif ext == ".png":
        watermarked.save(output, format="PNG")
    elif ext == ".webp":
        watermarked.save(output, format="WEBP", quality=95)
    output.seek(0)
    return output


def process_pdf(data: bytes, text: str) -> io.BytesIO:
    """Rasterise every PDF page, apply watermark, rebuild as image-only PDF."""
    doc = fitz.open(stream=data, filetype="pdf")
    pages: list[Image.Image] = []
    overlay_cache: dict[tuple[int, int], Image.Image] = {}

    for page in doc:
        pix = page.get_pixmap(dpi=RASTERIZE_DPI)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
        key = (img.width, img.height)
        if key not in overlay_cache:
            overlay_cache[key] = create_watermark_overlay(img.width, img.height, text)
        img = Image.alpha_composite(img, overlay_cache[key]).convert("RGB")
        pages.append(img)
    doc.close()

    output = io.BytesIO()
    pages[0].save(
        output, format="PDF", resolution=RASTERIZE_DPI,
        save_all=len(pages) > 1, append_images=pages[1:] if len(pages) > 1 else [],
    )
    output.seek(0)
    return output


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.post("/watermark")
async def watermark(
    file: UploadFile = File(...),
    watermark_text: str = Form(default=""),
):
    text = watermark_text.strip() or WATERMARK_TEXT
    text = " — ".join(line.strip() for line in text.splitlines() if line.strip())
    if "{date}" in text:
        text = text.replace("{date}", date.today().strftime("%d/%m/%Y"))

    filename = file.filename or "file"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")

    result = process_image(data, ext, text) if ext in IMAGE_EXTENSIONS else process_pdf(data, text)

    return StreamingResponse(
        result,
        media_type=MEDIA_TYPES.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'inline; filename="watermarked_{filename}"'},
    )


# Static files (must be last so API routes take priority)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
