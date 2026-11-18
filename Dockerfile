# Oversyte demo container (used by the free Hugging Face Space; works on any Docker host too).
FROM python:3.12-slim

# OpenCV needs these system libraries
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && pip install --no-cache-dir -r requirements.txt huggingface_hub

COPY . .

# Hugging Face runs containers as user 1000
RUN useradd -m -u 1000 user && chown -R user /app
USER user
ENV PYTHONUNBUFFERED=1 PORT=7860
EXPOSE 7860

CMD ["python", "deploy/start_demo.py"]
