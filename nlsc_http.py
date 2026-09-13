"""連外服務的共用設定：NLSC 的 TLS 相容處理與 Overpass 的請求標頭。

這兩件事看起來瑣碎，但都是「少了就整個掛掉」的等級，所以獨立成一支模組
而不是散落在各處：

* NlscSSLAdapter —— NLSC 的憑證鏈缺少 Subject Key Identifier，
  Python 3.13 起 OpenSSL 預設啟用 VERIFY_X509_STRICT 會直接驗證失敗。
  這裡只關掉 X509_STRICT 這條規則，憑證鏈、主機名稱與有效期限
  仍然照驗，不是 verify=False。

* OVERPASS_HEADERS —— Overpass 公用端點會拒絕沒有有意義 User-Agent
  的請求（回 406／429），必須帶。
"""

from __future__ import annotations

import ssl
from typing import Any

from requests.adapters import HTTPAdapter

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

OVERPASS_HEADERS = {
    "User-Agent": "ntpc-zone-map/1.0 (land valuation research)",
    "Accept": "application/json",
}


class NlscSSLAdapter(HTTPAdapter):
    """NLSC 憑證鏈缺少 Subject Key Identifier。

    僅停用 OpenSSL X509_STRICT 規則，仍保留憑證鏈、主機名稱及
    憑證有效期限驗證。
    """

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        context = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        pool_kwargs["ssl_context"] = context
        super().init_poolmanager(
            connections,
            maxsize,
            block=block,
            **pool_kwargs,
        )
