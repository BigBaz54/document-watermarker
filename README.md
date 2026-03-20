# document-watermarker

Web app that applies diagonal tiled watermarks to PDFs and images. PDFs are fully rasterised — no extractable text remains in the output. Watermark lines alternate between blue, red, black and grey.

## Deploy

```bash
docker network create internal  # only once, if not already created by Caddy
docker compose up -d
```

## Configuration

Environment variables (set in `compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `WATERMARK_TEXT` | `Copie destinée à : — Usage : — Date : {date}` | Default watermark text. `{date}` is replaced with today's date |
| `RASTERIZE_DPI` | `300` | DPI for PDF page rasterisation |
| `MAX_FILE_SIZE_MB` | `50` | Per-file upload size limit |
| `WATERMARK_OPACITY` | `100` | Text opacity (0–255) |
| `WATERMARK_ROWS` | `6` | Number of watermark rows on a portrait A4 page |
