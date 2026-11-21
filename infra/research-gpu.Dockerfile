# Isolated research image. Never replaces infra-worker or serving A.
FROM infra-worker:latest
RUN pip install --no-cache-dir \
    "https://download-r2.pytorch.org/whl/cu128/torch-2.11.0%2Bcu128-cp311-cp311-manylinux_2_28_x86_64.whl#sha256=c9a7ca4c74fae10a58e6175b4b2cea953f9322bb6562bbf339ad6a05f52190ad" \
    "https://download-r2.pytorch.org/whl/cu128/torchvision-0.26.0%2Bcu128-cp311-cp311-manylinux_2_28_x86_64.whl#sha256=8f2629d056570c929b0a1d5473d9cb0320b90bda1764bda353553a72cc6b2069" \
    einops==0.8.1 rich==14.2.0 tqdm==4.67.1
RUN pip install --no-cache-dir --no-deps \
    "https://github.com/Parskatt/RoMaV2/archive/95c9968145c8906b7b59383258e9f73b02853d89.zip"
ENV OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
