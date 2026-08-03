FROM python:3.12-slim

# Tesseract for the V2 OCR pass. eng language pack ships with tesseract-ocr.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Pillow to reconstruct raw-pixel (Flate-compressed) images into a format
# Tesseract accepts. Small (~15 MB); no other Python deps in V2.
RUN pip install --no-cache-dir 'pillow~=11.0'

WORKDIR /app

# v4 only — the pipeline is standalone. v1/v2/v3 exist in the repo as
# frozen reference and are deliberately NOT shipped: the image building
# without them is the proof of standalone-ness.
COPY run.sh solution.py /app/
COPY v4/ /app/v4/
RUN chmod +x /app/run.sh && mkdir -p /input /output

ENTRYPOINT ["/app/run.sh"]
