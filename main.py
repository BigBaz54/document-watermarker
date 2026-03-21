import io
import logging
import math
import os
import threading
import time
import uuid
from datetime import date
from pathlib import Path

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
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
FILE_TTL = int(os.getenv("FILE_TTL", "3600"))  # seconds

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
# In-memory file store (TTL = 1h)
# ---------------------------------------------------------------------------
file_store: dict[str, dict] = {}
store_lock = threading.Lock()


def store_file(filename: str, data: bytes, media_type: str, status: str = "ready") -> str:
    file_id = uuid.uuid4().hex[:12]
    with store_lock:
        file_store[file_id] = {
            "filename": filename,
            "data": data,
            "media_type": media_type,
            "created": time.time(),
            "status": status,
        }
    return file_id


def process_in_background(file_id: str, data: bytes, ext: str, text: str, out_name: str):
    try:
        result = process_image(data, ext, text) if ext in IMAGE_EXTENSIONS else process_pdf(data, text)
        media_type = MEDIA_TYPES.get(ext, "application/octet-stream")
        with store_lock:
            file_store[file_id]["data"] = result.read()
            file_store[file_id]["media_type"] = media_type
            file_store[file_id]["filename"] = out_name
            file_store[file_id]["status"] = "ready"
    except Exception as e:
        log.error("Failed to process %s: %s", file_id, e)
        with store_lock:
            file_store[file_id]["status"] = "error"


def cleanup_expired():
    now = time.time()
    with store_lock:
        expired = [k for k, v in file_store.items() if now - v["created"] > FILE_TTL]
        for k in expired:
            del file_store[k]


def start_cleanup_thread():
    def loop():
        while True:
            time.sleep(60)
            cleanup_expired()
    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Document Watermarker")


@app.on_event("startup")
def on_startup():
    start_cleanup_thread()


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if os.path.exists(FONT_PATH):
        return ImageFont.truetype(FONT_PATH, size)
    return ImageFont.load_default(size)


def create_watermark_overlay(width: int, height: int, text: str) -> Image.Image:
    """Create a transparent overlay with diagonal tiled watermark text."""
    ref = int(width * A4_RATIO) if width > height else height

    num_rows = WATERMARK_ROWS
    step_y = ref // num_rows
    font_size = max(12, step_y // 10)
    font = _load_font(font_size)

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
    image.load()
    image = image.convert("RGB").convert("RGBA")
    overlay = create_watermark_overlay(image.width, image.height, text)
    return Image.alpha_composite(image, overlay)


def process_image(data: bytes, ext: str, text: str) -> io.BytesIO:
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
    apply: str = Form(default="true"),
):
    filename = file.filename or "file"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")

    media_type = MEDIA_TYPES.get(ext, "application/octet-stream")

    if apply == "true":
        text = watermark_text.strip() or WATERMARK_TEXT
        text = " — ".join(line.strip() for line in text.splitlines() if line.strip())
        if "{date}" in text:
            text = text.replace("{date}", date.today().strftime("%d/%m/%Y"))
        out_name = f"{Path(filename).stem}_watermarked{ext}"
        file_id = store_file(out_name, b"", media_type, status="processing")
        threading.Thread(
            target=process_in_background,
            args=(file_id, data, ext, text, out_name),
            daemon=True,
        ).start()
    else:
        file_id = store_file(filename, data, media_type)

    return JSONResponse({"id": file_id, "filename": filename})


@app.get("/files")
async def list_files():
    cleanup_expired()
    now = time.time()
    with store_lock:
        items = [
            {
                "id": k,
                "filename": v["filename"],
                "expires_in": int(FILE_TTL - (now - v["created"])),
                "status": v.get("status", "ready"),
            }
            for k, v in file_store.items()
        ]
    items.sort(key=lambda x: x["expires_in"])
    return JSONResponse(items)


@app.get("/files/{file_id}")
async def download_file(file_id: str):
    with store_lock:
        entry = file_store.get(file_id)
    if not entry or time.time() - entry["created"] > FILE_TTL:
        raise HTTPException(404, "File expired or not found")
    return Response(
        content=entry["data"],
        media_type=entry["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{entry["filename"]}"'},
    )


# Static files (must be last so API routes take priority)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
