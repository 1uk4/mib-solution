FROM python:3.12-slim

# Tesseract for the V2 OCR pass. eng language pack ships with tesseract-ocr.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Pillow to reconstruct raw-pixel (Flate-compressed) images into a format
# Tesseract accepts. Small (~15 MB); no other Python deps in V2.
RUN pip install --no-cache-dir 'pillow~=11.0'

WORKDIR /app

COPY run.sh solution.py /app/
COPY v1/ /app/v1/
COPY v2/ /app/v2/
COPY v3/ /app/v3/
RUN chmod +x /app/run.sh && mkdir -p /input /output

ENTRYPOINT ["/app/run.sh"]
