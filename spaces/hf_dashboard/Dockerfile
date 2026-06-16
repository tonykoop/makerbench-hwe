# MakerBench HWE — dual-league dashboard Docker Space (#98).
#
# A Docker SDK Space (NOT the sdk:static mirror staged by scripts/build_hf_space.py)
# so we can ship headless geometry libs (trimesh + a STEP/B-Rep reader) for the
# Inspect-a-Run 3D viewer and wire the regrade worker to hidden seeds delivered
# via HF repo SECRETS at runtime. Golden seeds are NEVER baked into this image.
#
# Build context is the repo root (the app imports the sibling dashboard_data.py
# and the scripts/ run-dir reader, and reads public results/).

FROM python:3.11-slim

# Headless OpenGL / OpenCASCADE runtime libs that the geometry wheels dlopen.
# (libGL/libgl1 for trimesh's renderers; libXrender/libSM/libICE for OCP.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglu1-mesa \
        libxrender1 \
        libxext6 \
        libsm6 \
        libice6 \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run as a non-root user (uid 1000) with $HOME=/home/user.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860

WORKDIR /home/user/app
USER user

# Install Python deps first for layer caching.
COPY --chown=user spaces/hf_dashboard/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy the app + its stdlib data layer, the shared run-dir reader/library it
# imports, and the public results tree (data only — no private/ ever).
COPY --chown=user spaces/hf_dashboard/ ./spaces/hf_dashboard/
COPY --chown=user scripts/generate_run_explorer.py ./scripts/generate_run_explorer.py
COPY --chown=user scripts/generate_run_library.py ./scripts/generate_run_library.py
COPY --chown=user site/ ./site/
COPY --chown=user results/ ./results/

EXPOSE 7860

# app.py resolves the repo root from its own location (parents[1]); running from
# /home/user/app keeps spaces/hf_dashboard/dashboard_data.py + scripts/ importable.
CMD ["python", "spaces/hf_dashboard/app.py", "--port", "7860"]
