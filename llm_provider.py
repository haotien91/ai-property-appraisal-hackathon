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

設定（非機密，可以放環境變數）
--------------------------
    AWS_REGION          Bedrock 區域，例 us-east-1
    BEDROCK_MODEL_ID    模型 ID，例 global.amazon.nova-2-lite-v1:0
    LLM_PROVIDER        bedrock | echo（預設 bedrock）
    AWS_PROFILE         本機開發用的 ~/.aws profile 名稱（正式環境不要設）

憑證（機密，不要放環境變數）
-------------------------
本模組不接收憑證參數。boto3 會依序自動解析：

    1. 環境變數 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
    2. ~/.aws/credentials 的 profile
    3. 容器憑證供應者（ECS / Fargate 的 task role）
    4. EC2 instance metadata（instance profile）

部署到 ECS/Fargate 請走第 3 條 —— **機器上不存在任何長期金鑰**，
由 IAM task role 提供自動輪替的臨時憑證。這是唯一該用的方式。

不要把 AWS_ACCESS_KEY_ID 寫進 ECS task definition 的 environment：
那個欄位在 console 與 describe-task-definition 都看得到，等於明文外洩。
真的需要長期金鑰時（例如在 AWS 之外執行）才用 Secrets Manager，
並透過 task definition 的 secrets 欄位注入。

需要的 IAM 權限只有 bedrock:InvokeModel。
"""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger("llm_provider")

LlmCallable = Callable[[str], str]


# ---------------------------------------------------------------------------
# .env 載入
#
# os.environ 只看得到 OS／shell 已經注入行程的變數，讀不到 .env 檔案，
# 所以必須自己解析並注入。這裡手寫三十行而不引入 python-dotenv，
# 是因為只需要覆蓋兩個關鍵語意：
#
#   1. 預設不覆寫已存在的環境變數。容器裡由 ECS task definition 注入的值
#      必須勝過映像內殘留的 .env，否則部署設定會被開發用設定蓋掉。
#   2. 找不到 .env 不是錯誤。正式部署本來就不該有 .env，
#      所有設定都來自 task definition 與 task role。
# ---------------------------------------------------------------------------

DEFAULT_ENV_PATH = Path(__file__).parent / ".env"

# 這些鍵屬機密，回報載入結果時只顯示鍵名不顯示值，避免寫進日誌。
_SECRET_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def is_secret(key: str) -> bool:
    return any(hint in key.upper() for hint in _SECRET_HINTS)


def parse_env_text(text: str) -> dict[str, str]:
    """解析 .env 內容。支援 # 註解、export 前綴、單雙引號。"""

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 先處理引號：引號內的 # 不算註解。
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            hash_at = value.find(" #")
            if hash_at >= 0:
                value = value[:hash_at].rstrip()
        if key:
            values[key] = value
    return values


def load_env_file(
    path: str | Path | None = None,
    *,
    override: bool = False,
) -> list[str]:
    """把 .env 的內容放進 os.environ，回傳實際套用的鍵名清單。

    找不到檔案時回空清單而非拋錯，因為正式部署不該有 .env。
    """

    target = Path(path) if path else DEFAULT_ENV_PATH
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []

    applied: list[str] = []
    for key, value in parse_env_text(text).items():
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


def describe_loaded(keys: list[str]) -> str:
    """載入結果的一行摘要；機密只顯示鍵名不顯示值。"""

    if not keys:
        return "未載入 .env（檔案不存在或所有鍵已由環境變數提供）"
    secret = sorted(k for k in keys if is_secret(k))
    plain = sorted(k for k in keys if not is_secret(k))
    parts = []
    if plain:
        parts.append("設定 " + "、".join(plain))
    if secret:
        parts.append(f"機密 {len(secret)} 項（{'、'.join(secret)}）")
    return "已從 .env 載入：" + "；".join(parts)

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


# 跨區推論（CRIS）的地區前綴。Nova 2 等模型在美國以外呼叫時，
# 裸的 model id 會被拒，必須改用帶前綴的 inference profile id，
# 例如 amazon.nova-2-lite-v1:0 → global.amazon.nova-2-lite-v1:0
_CRIS_PREFIXES = ("global", "us", "eu", "apac", "jp")


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
    profile: str | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    attempts: int = 4,
    read_timeout_seconds: int = 120,
    client=None,
) -> LlmCallable:
    """建立呼叫 Bedrock Converse API 的函式。

    憑證不由本函式接收，也不應該由本函式接收。boto3 會依序自動解析：

        1. 環境變數 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
        2. ~/.aws/credentials 的 profile（本機開發建議用這個）
        3. 容器憑證供應者（ECS/Fargate 的 task role）
        4. EC2 instance metadata（instance profile）

    部署到 ECS/Fargate 時走第 3 條，機器上不存在任何長期金鑰，
    也不需要輪替。這是唯一該用的方式。
    profile 參數只是本機開發的便利選項，正式環境請留空。

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

    resolved_profile = profile or os.environ.get("AWS_PROFILE")

    if client is None:
        session = boto3.Session(
            profile_name=resolved_profile, region_name=resolved_region
        )
        client = session.client(
            "bedrock-runtime",
            # 關掉 botocore 內建重試，改由 _with_retry 統一處理，
            # 否則兩層重試相乘，最壞情況會等非常久。
            config=Config(
                retries={"max_attempts": 1},
                read_timeout=read_timeout_seconds,
            ),
        )
    runtime = client

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


def describe_credentials(region: str | None = None) -> dict:
    """回報 boto3 實際解析到的憑證來源與身分，不輸出祕密值。

    診斷 AccessDenied 時第一個要確認的就是「我到底用了哪一組憑證」。
    boto3 會依序找環境變數、~/.aws/credentials、~/.aws/config 的 profile、
    容器或 EC2 的 IAM Role，很容易以為在用 A 其實在用 B。
    """

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    session = boto3.Session(region_name=region or os.environ.get("AWS_REGION"))
    report: dict = {
        "region": session.region_name,
        "profile": session.profile_name,
    }

    credentials = session.get_credentials()
    if credentials is None:
        report["credential_source"] = None
        report["problem"] = (
            "boto3 找不到任何憑證。請設定 AWS_ACCESS_KEY_ID 與 "
            "AWS_SECRET_ACCESS_KEY，或執行 aws configure"
        )
        return report

    # botocore 用 method 標示來源：env、shared-credentials-file、
    # assume-role、iam-role、container-role…
    report["credential_source"] = credentials.method
    frozen = credentials.get_frozen_credentials()
    report["access_key_id"] = frozen.access_key[:4] + "…" + frozen.access_key[-4:]
    report["has_session_token"] = bool(frozen.token)

    try:
        identity = session.client("sts").get_caller_identity()
        report["account"] = identity["Account"]
        report["arn"] = identity["Arn"]
    except (ClientError, BotoCoreError) as exc:
        report["problem"] = f"憑證存在但 STS 驗證失敗：{exc}"

    return report


def _explain_bedrock_error(exc: BaseException, model_id: str, region: str) -> str:
    """把 Bedrock 的錯誤碼翻成可操作的下一步。"""

    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    name = code or type(exc).__name__

    if name == "ValidationException":
        message = str(exc)
        # Nova 2 等模型在美國以外必須用 CRIS：裸 model id 會被拒。
        if "inference profile" in message or "on-demand" in message:
            # 前綴是加在完整 model id 之前，不是取代廠商名：
            # amazon.nova-2-lite-v1:0 → global.amazon.nova-2-lite-v1:0
            base = model_id
            for existing in _CRIS_PREFIXES:
                if base.startswith(f"{existing}."):
                    base = base[len(existing) + 1 :]
                    break
            options = "\n".join(
                f"    {prefix}.{base}" for prefix in _CRIS_PREFIXES
            )
            return (
                f"model id「{model_id}」不支援在 {region} 直接隨需呼叫。\n"
                "  這類模型需要跨區推論設定（CRIS），請改用下列任一種：\n"
                f"{options}\n"
                "  可用 `aws bedrock list-inference-profiles --region "
                f"{region}` 查詢實際可用的 profile id"
            )
        return f"參數有誤：{message}"

    if name == "AccessDeniedException":
        return (
            f"沒有權限呼叫「{model_id}」。兩件事都要確認：\n"
            "    1. IAM 政策是否允許 bedrock:InvokeModel\n"
            "    2. Bedrock console 的 Model access 是否已開啟該模型\n"
            "       （第一次使用需手動申請啟用）"
        )

    if name == "ResourceNotFoundException":
        return (
            f"在 {region} 找不到「{model_id}」。請確認 model id 拼寫，"
            f"或用 `aws bedrock list-foundation-models --region {region}` 查詢"
        )

    if name == "ThrottlingException":
        return "被限流。程式已內建退避重試，若持續發生請申請提高配額"

    if name in {"UnrecognizedClientException", "InvalidSignatureException"}:
        return (
            "憑證無效或已失效。若用臨時憑證請確認 AWS_SESSION_TOKEN 也設了，"
            "且尚未過期"
        )

    return f"{name}: {exc}"


if __name__ == "__main__":
    # 煙霧測試：實際打一次 Bedrock，確認憑證、region、model id 都對。
    #   $env:AWS_ACCESS_KEY_ID="..."
    #   $env:AWS_SECRET_ACCESS_KEY="..."
    #   $env:AWS_SESSION_TOKEN="..."        # 臨時憑證才需要
    #   $env:AWS_REGION="us-east-1"
    #   $env:BEDROCK_MODEL_ID="global.amazon.nova-2-lite-v1:0"
    #   python llm_provider.py
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=== .env ===")
    print(f"  {describe_loaded(load_env_file())}")

    region_env = os.environ.get("AWS_REGION")
    model_env = os.environ.get("BEDROCK_MODEL_ID")
    print("\n=== 設定 ===")
    print(f"  AWS_REGION       = {region_env}")
    print(f"  BEDROCK_MODEL_ID = {model_env}")

    print("\n=== 憑證 ===")
    try:
        for key, value in describe_credentials().items():
            print(f"  {key:18s} = {value}")
    except ImportError:
        print("  未安裝 boto3，請先 pip install boto3", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001 - 診斷用，任何問題都要看到
        print(f"  憑證檢查失敗：{exc}", file=sys.stderr)

    print("\n=== 呼叫 ===")
    try:
        llm = make_llm()
    except Exception as exc:  # noqa: BLE001 - 設定錯誤要講清楚
        print(f"  建立失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)

    question = sys.argv[1] if len(sys.argv) > 1 else "只回覆 OK 兩個字。"
    print(f"  prompt = {question}")
    print("-" * 60)
    try:
        print(llm(question))
    except Exception as exc:  # noqa: BLE001
        print(
            "\n呼叫失敗：\n  "
            + _explain_bedrock_error(exc, model_env or "?", region_env or "?"),
            file=sys.stderr,
        )
        raise SystemExit(1)
