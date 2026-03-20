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
WATERMARK_OPACITY = int(os.getenv("WATERMARK_OPACITY", "100"))
WATERMARK_ROWS = int(os.getenv("WATERMARK_ROWS", "6"))

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
    (96, 96, 96),
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
    font_size = max(12, step_y // 10)
    font = _load_font(font_size)

    # Build one long repeated line: "text     text     text     text"
    sep = "     "
    line = (text + sep) * 4

    colors = [(*c, WATERMARK_OPACITY) for c in WATERMARK_COLORS]

    diag = int(math.sqrt(width**2 + height**2))
    canvas = Image.new("RGBA", (diag * 2, diag * 2), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(canvas)

    total_rows = diag * 2 // step_y + 1
    for row in range(total_rows):
        cdraw.text((0, row * step_y), line, font=font, fill=colors[row % len(colors)])

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
