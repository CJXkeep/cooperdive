FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY copper ./copper
COPY daqin ./daqin
COPY storage ./storage
COPY scripts ./scripts
COPY tests ./tests
COPY .streamlit ./.streamlit

# 默认跑调度器；dashboard 服务在 compose 里覆盖为 streamlit
CMD ["python", "-m", "scripts.scheduler"]
