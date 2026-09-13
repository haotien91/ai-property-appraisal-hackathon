# 地價區段圖出圖服務
#
# 兩個容器內的關鍵前提：
#
# 1. 中文字型。Pillow 找不到 CJK 字型時會退回內建點陣字型，
#    路名與地號會整排變成豆腐框。fonts-noto-cjk 是必要相依，不是選配。
#
# 2. 新北市使用分區圖。這是唯讀參考資料（約 179 MB，一年可能才更新一次），
#    跟程式碼一起 bake 進 image 最單純：不需要 IAM、不需要啟動下載，
#    也不會出現程式與資料版本不一致。
#    要改成執行期由 S3 拉取，就把 COPY 那行拿掉、
#    並用 NTPC_ZONING_SHP 指到掛載路徑。

FROM python:3.13-slim AS base

# fonts-noto-cjk：中文標註的必要相依
# libexpat1／libstdc++6：pyogrio 與 shapely 的 wheel 需要
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 程式碼
COPY *.py /app/
COPY boundary_core /app/boundary_core

# 唯讀參考資料：使用分區圖
COPY ["新北市使用分區", "/app/新北市使用分區"]

# 快取目錄。正式部署請把這三個掛成 volume 或 EFS，
# 否則容器重啟就要重新向 Overpass 與 NLSC 抓一次（冷啟動 60～143 秒）。
ENV NLSC_TILE_CACHE=/var/cache/nlsc-tiles \
    ROAD_CACHE_DIR=/var/cache/nlsc-roads \
    ZONE_MAP_CACHE=/var/cache/zone-maps \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
RUN mkdir -p /var/cache/nlsc-tiles /var/cache/nlsc-roads /var/cache/zone-maps

# 不要用 root 跑服務
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /var/cache/nlsc-tiles /var/cache/nlsc-roads /var/cache/zone-maps
USER appuser

EXPOSE 8080

# --workers 1 是刻意的：分區圖與路網快取都在行程記憶體內，
# 多 worker 會各讀一份 170 MB。要擴充請水平加容器。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"

CMD ["uvicorn", "zone_map_api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
