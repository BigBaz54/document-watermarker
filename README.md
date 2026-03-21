# document-watermarker

Web app that applies diagonal tiled watermarks to PDFs and images. PDFs are fully rasterised — no extractable text remains in the output. Watermark lines alternate between blue, red, black and grey.

Files are processed in the background and stored in memory for 1 hour, so you can upload from one device and download from another.

## Deploy

```bash
docker network create internal  # only once, if not already created by Caddy
docker compose up -d
```

## Features

- Drag & drop or click to upload (PDF, JPG, PNG, WEBP)
- Watermark toggle — disable to use as a simple file share
- Background processing — page stays responsive during heavy PDFs
- History with download links, auto-refreshes, files expire after 1h

## Configuration

Environment variables (set in `compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `WATERMARK_TEXT` | `Copie destinée à : — Usage : — Date : {date}` | Default text. `{date}` → today's date |
| `RASTERIZE_DPI` | `300` | DPI for PDF rasterisation |
| `MAX_FILE_SIZE_MB` | `50` | Per-file upload limit |
| `WATERMARK_OPACITY` | `100` | Text opacity (0–255) |
| `WATERMARK_ROWS` | `6` | Rows on a portrait A4 page |
| `FILE_TTL` | `3600` | File retention in seconds |
