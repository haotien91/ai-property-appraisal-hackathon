"""LLM 供應者：Amazon Bedrock Converse API。

`getSectionCode.run_pipeline()` 收的是一個 callable：

    llm(prompt: str) -> str

本模組提供兩個實作，形狀相同可互換：

    make_bedrock_llm()   Amazon Bedrock Converse API
    make_echo_llm()      測試用，不連網

為什麼獨立成一支模組
------------------
憑證、model id、region、重試策略會隨部署環境改變，而 pipeline 的邏輯
（段籍查詢、JSON 校正、出圖）不會。混在一起的話換模型就要動到已驗證過
的流程。

為什麼選 Converse 而不是 InvokeModel
----------------------------------
Converse 對各家模型統一同一組 request/response 結構，換 model id 就換模型，
不必改 payload 格式。InvokeModel 是各模型原生格式，只有需要特定模型獨有
功能時才需要。

為什麼不用 SageMaker
-------------------
SageMaker realtime endpoint 是「開著就計費」的 GPU 實例。本專案一個區段只
呼叫 LLM 一次，且是使用者觸發式，絕大多數時間閒置。Bedrock 按 token 計費，
沒呼叫就不花錢。

環境變數
-------
    AWS_REGION          Bedrock 區域，例 us-east-1
    BEDROCK_MODEL_ID    模型 ID
    LLM_PROVIDER        bedrock | echo（預設 bedrock）

憑證由 boto3 依標準順序解析（環境變數 → ~/.aws → IAM Role）。
在 ECS/Fargate 上請用 task role，不要把金鑰寫進程式或環境變數。
需要的 IAM 權限只有 bedrock:InvokeModel。
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Callable

logger = logging.getLogger("llm_provider")

LlmCallable = Callable[[str], str]

# 出圖流程對 LLM 的要求：只輸出 JSON、不要多話。
DEFAULT_SYSTEM_PROMPT = (
    "你是地價區段資料的結構化助手。"
    "只輸出使用者要求的 JSON，不要加說明文字，不要加 markdown 圍欄。"
)

# 產生結構化 JSON 要低隨機性，否則同樣輸入會拆出不同結果。
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 2048

# Bedrock 限流與暫時性錯誤的例外名稱。用名稱比對而不 import botocore 的
# 例外類別，是為了讓本模組在沒裝 boto3 的環境也能被 import（例如只跑
# 不連網測試時）。
_RETRYABLE_ERROR_NAMES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "InternalServerException",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "EndpointConnectionError",
}


class TransientLlmError(RuntimeError):
    """暫時性故障，重試可能成功。"""


class PermanentLlmError(RuntimeError):
    """確定性失敗，重試不會有不同結果。

    例如回應因達到 maxTokens 而截斷 —— 同樣的輸入必然再次截斷，
    重試只是把失敗延後。要解決得調高 max_tokens 或縮短 prompt。
    """


def _is_retryable(exc: BaseException) -> bool:
    """限流／逾時／暫時性故障值得重試；設定錯誤與權限不足不該重試。

    ValidationException（model id 打錯）或 AccessDeniedException（IAM 沒開）
    重試四次只是把失敗延後 20 秒，還會掩蓋真正的原因。
    """

    if isinstance(exc, PermanentLlmError):
        return False
    if isinstance(exc, TransientLlmError):
        return True
    name = type(exc).__name__
    if name in _RETRYABLE_ERROR_NAMES:
        return True
    # botocore 的 ClientError 把服務端錯誤碼放在 response 裡。
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    return code in _RETRYABLE_ERROR_NAMES


def _with_retry(
    call: LlmCallable,
    *,
    attempts: int = 4,
    base_delay_seconds: float = 1.5,
) -> LlmCallable:
    """指數退避重試，只重試值得重試的錯誤。

    出圖流程一個區段只呼叫一次 LLM，重試的代價遠低於整案失敗。
    退避加入抖動，避免多個請求同時重試又一起被擋。
    """

    def wrapper(prompt: str) -> str:
        last: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return call(prompt)
            except Exception as exc:  # noqa: BLE001 - 由 _is_retryable 分流
                last = exc
                if not _is_retryable(exc):
                    raise
                if attempt == attempts:
                    break
                delay = base_delay_seconds * (2 ** (attempt - 1))
                delay += random.uniform(0, delay * 0.3)
                logger.warning(
                    "Bedrock 呼叫失敗（第 %d/%d 次）：%s: %s；%.1f 秒後重試",
                    attempt,
                    attempts,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError(
            f"Bedrock 呼叫在 {attempts} 次嘗試後仍失敗：{last}"
        ) from last

    return wrapper


def make_bedrock_llm(
    model_id: str | None = None,
    *,
    region: str | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    attempts: int = 4,
    read_timeout_seconds: int = 120,
    client=None,
) -> LlmCallable:
    """建立呼叫 Bedrock Converse API 的函式。

    client 可注入自訂的 boto3 client，方便測試或指定 endpoint_url。
    """

    import boto3
    from botocore.config import Config

    resolved_model = model_id or os.environ.get("BEDROCK_MODEL_ID")
    if not resolved_model:
        raise ValueError(
            "請提供 model_id 或設定環境變數 BEDROCK_MODEL_ID；"
            "可用 `aws bedrock list-foundation-models` 查詢可用模型"
        )

    resolved_region = region or os.environ.get("AWS_REGION")
    if not resolved_region:
        raise ValueError("請提供 region 或設定環境變數 AWS_REGION")

    runtime = client or boto3.client(
        "bedrock-runtime",
        region_name=resolved_region,
        # 關掉 botocore 內建重試，改由 _with_retry 統一處理，
        # 否則兩層重試相乘，最壞情況會等非常久。
        config=Config(
            retries={"max_attempts": 1},
            read_timeout=read_timeout_seconds,
        ),
    )

    def call(prompt: str) -> str:
        response = runtime.converse(
            modelId=resolved_model,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "temperature": temperature,
                "maxTokens": max_tokens,
            },
        )
        blocks = response["output"]["message"]["content"]
        text = "".join(block.get("text", "") for block in blocks).strip()

        stop_reason = response.get("stopReason")
        if stop_reason == "max_tokens":
            # 截斷是確定性的，重試不會有不同結果，直接給可操作的建議。
            raise PermanentLlmError(
                f"回應因達到 maxTokens({max_tokens}) 而截斷，JSON 不完整；"
                "請調高 max_tokens 或縮短 prompt"
            )
        if not text:
            raise TransientLlmError(
                f"Bedrock 回傳空內容，stopReason={stop_reason}"
            )

        usage = response.get("usage", {})
        logger.info(
            "Bedrock %s：輸入 %s tokens、輸出 %s tokens、stopReason=%s",
            resolved_model,
            usage.get("inputTokens"),
            usage.get("outputTokens"),
            stop_reason,
        )
        return text

    return _with_retry(call, attempts=attempts)


def make_echo_llm(response: str) -> LlmCallable:
    """測試用：不連網，固定回傳給定內容。

    讓 pipeline 的驗證與校正邏輯可以在沒有 AWS 憑證的環境下測試。
    """

    def call(_prompt: str) -> str:
        return response

    return call


def make_llm(provider: str | None = None, **kwargs) -> LlmCallable:
    """依環境變數或參數挑一個實作，讓呼叫端不必寫 if/else。"""

    resolved = (provider or os.environ.get("LLM_PROVIDER") or "bedrock").lower()
    if resolved == "bedrock":
        return make_bedrock_llm(**kwargs)
    if resolved == "echo":
        return make_echo_llm(kwargs.get("response", "[]"))
    raise ValueError(
        f"不支援的 LLM_PROVIDER：{resolved}；可用值 bedrock、echo"
    )


if __name__ == "__main__":
    # 煙霧測試：實際打一次 Bedrock，確認憑證、region、model id 都對。
    #   $env:AWS_REGION="us-east-1"
    #   $env:BEDROCK_MODEL_ID="<model id>"
    #   python llm_provider.py
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"AWS_REGION       = {os.environ.get('AWS_REGION')}")
    print(f"BEDROCK_MODEL_ID = {os.environ.get('BEDROCK_MODEL_ID')}")
    try:
        llm = make_llm()
    except Exception as exc:  # noqa: BLE001 - 設定錯誤要講清楚
        print(f"\n建立 LLM 失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)

    question = sys.argv[1] if len(sys.argv) > 1 else "只回覆 OK 兩個字。"
    print(f"prompt = {question}")
    print("-" * 50)
    try:
        print(llm(question))
    except Exception as exc:  # noqa: BLE001
        print(f"\n呼叫失敗：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
