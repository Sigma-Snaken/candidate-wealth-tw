FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt
COPY api/ api/
COPY pipeline/ pipeline/
COPY dist/data.json dist/data.json
COPY dist/detail/ dist/detail/
COPY data/db/candidate_wealth.sqlite data/db/candidate_wealth.sqlite
# Noto Sans CJK TC 在這些 TTC 的 face index 3；Medium 字重在 fonts-noto-cjk-extra（約 600 MB），內文改用 Regular（容器內以 ImageFont.truetype(...).getname() 逐一列出確認）
ENV BILLBOARD_FONT=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    BILLBOARD_FONT_BOLD=/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc \
    BILLBOARD_FONT_INDEX=3
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}
