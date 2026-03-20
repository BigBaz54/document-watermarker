# document-watermarker

Web app that applies diagonal tiled watermarks to PDFs and images. PDFs are fully rasterised — no extractable text remains in the output.

## Deploy

```bash
docker compose up -d
```

If the `internal` network doesn't exist yet:

```bash
docker network create internal
```

## Configuration

Environment variables (set in `compose.yml` or copy `.env.example` → `.env`):

| Variable | Default | Description |
|---|---|---|
| `WATERMARK_TEXT` | `CONFIDENTIEL` | Default watermark text |
| `RASTERIZE_DPI` | `300` | DPI for PDF page rasterisation |
| `MAX_FILE_SIZE_MB` | `50` | Per-file upload size limit |
