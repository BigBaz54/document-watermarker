import io
import math
import os
from pathlib import Path

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "CONFIDENTIEL")
RASTERIZE_DPI = int(os.getenv("RASTERIZE_DPI", "300"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
WATERMARK_FONT_SIZE = int(os.getenv("WATERMARK_FONT_SIZE", "80"))
WATERMARK_OPACITY = int(os.getenv("WATERMARK_OPACITY", "64"))

MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Document Watermarker")


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def create_watermark_overlay(width: int, height: int, text: str) -> Image.Image:
    """Create a transparent overlay with diagonal tiled watermark text."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    # Scale font size relative to image — at least WATERMARK_FONT_SIZE,
    # but grow for high-res images so the watermark stays visible
    font_size = max(WATERMARK_FONT_SIZE, min(width, height) // 20)
    font = _load_font(font_size)

    # Measure text size
    tmp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Create a single rotated text stamp
    padding = 20
    stamp_size = int(math.sqrt((text_w + padding) ** 2 + (text_h + padding) ** 2)) + 4
    stamp = Image.new("RGBA", (stamp_size, stamp_size), (0, 0, 0, 0))
    stamp_draw = ImageDraw.Draw(stamp)
    tx = (stamp_size - text_w) // 2
    ty = (stamp_size - text_h) // 2
    stamp_draw.text(
        (tx, ty), text, font=font, fill=(128, 128, 128, WATERMARK_OPACITY)
    )
    stamp = stamp.rotate(45, resample=Image.BICUBIC, expand=False)

    # Tile across the image
    step_x = int(text_w * 1.8)
    step_y = int(text_h * 4.0)

    # Extend beyond bounds to cover corners
    margin = stamp_size
    y = -margin
    while y < height + margin:
        x = -margin
        while x < width + margin:
            overlay.paste(stamp, (x, y), stamp)
            x += step_x
        y += step_y

    return overlay


def apply_watermark_to_image(image: Image.Image, text: str) -> Image.Image:
    """Apply watermark overlay to a PIL Image."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    overlay = create_watermark_overlay(image.width, image.height, text)
    return Image.alpha_composite(image, overlay)


def process_image(data: bytes, ext: str, text: str) -> io.BytesIO:
    """Watermark a single image and return bytes in the same format."""
    image = Image.open(io.BytesIO(data))
    watermarked = apply_watermark_to_image(image, text)

    output = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        watermarked = watermarked.convert("RGB")
        watermarked.save(output, format="JPEG", quality=95)
    elif ext == ".png":
        watermarked.save(output, format="PNG")
    elif ext == ".webp":
        watermarked.save(output, format="WEBP", quality=95)
    output.seek(0)
    return output


def process_pdf(data: bytes, text: str) -> io.BytesIO:
    """Rasterise every PDF page, apply watermark, rebuild as image-only PDF."""
    doc = fitz.open(stream=data, filetype="pdf")
    watermarked_pages: list[Image.Image] = []
    overlay_cache: dict[tuple[int, int], Image.Image] = {}

    for page in doc:
        pix = page.get_pixmap(dpi=RASTERIZE_DPI)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = img.convert("RGBA")
        key = (img.width, img.height)
        if key not in overlay_cache:
            overlay_cache[key] = create_watermark_overlay(img.width, img.height, text)
        img = Image.alpha_composite(img, overlay_cache[key])
        img = img.convert("RGB")
        watermarked_pages.append(img)

    doc.close()

    output = io.BytesIO()
    if len(watermarked_pages) == 1:
        watermarked_pages[0].save(output, format="PDF", resolution=RASTERIZE_DPI)
    else:
        watermarked_pages[0].save(
            output,
            format="PDF",
            save_all=True,
            append_images=watermarked_pages[1:],
            resolution=RASTERIZE_DPI,
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

    # Validate extension
    filename = file.filename or "file"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Read and validate size
    data = await file.read()
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit.",
        )

    # Process
    if ext in IMAGE_EXTENSIONS:
        result = process_image(data, ext, text)
    else:
        result = process_pdf(data, text)

    out_name = f"watermarked_{filename}"
    media_type = MEDIA_TYPES.get(ext, "application/octet-stream")

    return StreamingResponse(
        result,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{out_name}"'},
    )


# ---------------------------------------------------------------------------
# Static files (must be last so API routes take priority)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
