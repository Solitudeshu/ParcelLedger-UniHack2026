# Optional image-feature encoder runtime; build infra-worker first.
FROM infra-worker:latest
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.11.0+cpu torchvision==0.26.0+cpu
ENV OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
