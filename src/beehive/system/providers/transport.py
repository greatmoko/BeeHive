"""模型 HTTP 传输与取消、重试。"""
import json
import logging
import threading
import time
import urllib.request
import urllib.error
from collections.abc import Callable
from beehive.common.errors import AIError
from beehive.system.providers.request_errors import handle_http_error, is_retryable

logger = logging.getLogger("tokbee")


class AITransport:
    """连接、重试及取消状态的唯一所有者。"""

    def __init__(self, endpoint: str, api_key: str, protocol: str, *,
                 retry_interval: int = 0, call_delay: float = 0):
        self._url = self._build_url(endpoint, protocol)
        self._api_key = api_key
        self._retry_interval = max(0, int(retry_interval))
        self._call_delay = max(0.0, float(call_delay))
        self.on_retry_log: Callable[[str], None] | None = None
        self.cancel_check: Callable[[], bool] | None = None

    @staticmethod
    def _build_url(endpoint: str, protocol: str = "chat") -> str:
        url = endpoint.rstrip("/")
        target = "/responses" if protocol == "responses" else "/chat/completions"
        for suffix in ["/chat/completions", "/responses", "/completions"]:
            if url.endswith(suffix):
                return url[: -len(suffix)] + target
        if url.endswith("/models"):
            url = url[: -len("/models")]
        url = url.rstrip("/")
        return url + target

    def _make_request(self, body: dict) -> urllib.request.Request:
        payload_body = dict(body)
        extra = payload_body.pop("extra_body", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k not in payload_body:
                    payload_body[k] = v
        payload = json.dumps(payload_body, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            self._url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "Connection": "close",
            },
        )

    def _cancellable_sleep(self, seconds: float) -> bool:
        elapsed = 0.0
        step = 0.5
        while elapsed < seconds:
            if self.cancel_check and self.cancel_check():
                return True
            chunk = min(step, seconds - elapsed)
            time.sleep(chunk)
            elapsed += chunk
        return bool(self.cancel_check and self.cancel_check())

    def _emit_retry(self, wait: int, count: int):
        msg = f"触发限速 (HTTP 429)，{wait}秒后重试（第{count}次）…"
        logger.warning(msg)
        if self.on_retry_log:
            try:
                self.on_retry_log(msg)
            except Exception:
                pass

    def _apply_call_delay(self):
        if self._call_delay > 0:
            if self._cancellable_sleep(self._call_delay):
                raise AIError("用户取消")

    def _retry_http_error(self, error: urllib.error.HTTPError, attempt: int, max_attempts: int):
        """普通与流式连接共用分类及有限退避，额度/鉴权等错误立即返回。"""
        try:
            try:
                handle_http_error(error)
            finally:
                error.close()
        except AIError as classified:
            if not getattr(classified.kind, "retryable", False) or attempt >= max_attempts - 1:
                raise
            wait = self._retry_interval if error.code == 429 and self._retry_interval > 0 else 2 ** attempt
            if error.code == 429:
                self._emit_retry(wait, attempt + 1)
            else:
                logger.warning("API 请求失败 (HTTP %d，第%d次)，%ds后重试", error.code, attempt + 1, wait)
            if self._cancellable_sleep(wait):
                raise AIError("用户取消") from classified

    def _do_single_request(self, body: dict, timeout: int = 180) -> dict:
        req = self._make_request(body)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _request_with_cancel(self, body: dict, timeout: int = 180) -> dict:
        result_holder: list = []
        error_holder: list = []

        def _worker():
            try:
                result_holder.append(self._do_single_request(body, timeout))
            except Exception as exc:
                error_holder.append(exc)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        while t.is_alive():
            if self.cancel_check and self.cancel_check():
                raise AIError("用户取消")
            t.join(timeout=0.5)
        if error_holder:
            raise error_holder[0]
        if result_holder:
            return result_holder[0]
        raise AIError("请求未返回结果")

    def request(self, body: dict, timeout: int = 180, max_retries: int = 3) -> dict:
        self._apply_call_delay()
        last_exc: Exception | None = None
        attempt = 0
        while attempt < max_retries:
            try:
                return self._request_with_cancel(body, timeout)
            except urllib.error.HTTPError as e:
                self._retry_http_error(e, attempt, max_retries)
                attempt += 1
            except (RuntimeError, AIError):
                raise
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1 and is_retryable(e):
                    wait = 2 ** attempt
                    logger.warning("请求失败 (第%d次), %ds后重试: %s", attempt + 1, wait, e)
                    if self._cancellable_sleep(wait):
                        raise AIError("用户取消") from e
                    attempt += 1
                    continue
                if isinstance(e, urllib.error.URLError):
                    raise AIError(f"网络连接失败: {e.reason}", kind="transient") from e
                if isinstance(e, TimeoutError):
                    raise AIError("请求超时，请检查网络连接或 API 地址", kind="transient") from None
                raise AIError(f"请求异常: {e}", kind="unknown") from e
        raise AIError(f"网络连接失败 (重试{max_retries}次后仍失败): {last_exc}", kind="transient") from last_exc

    def open_stream(self, body: dict):
        self._apply_call_delay()
        last_exc: Exception | None = None
        resp = None
        max_retries = 3
        attempt = 0
        while attempt < max_retries:
            try:
                req = self._make_request(dict(body))
                resp = urllib.request.urlopen(req, timeout=180)
                break
            except urllib.error.HTTPError as e:
                self._retry_http_error(e, attempt, max_retries)
                attempt += 1
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1 and is_retryable(e):
                    wait = 2 ** attempt
                    logger.warning("流式请求失败 (第%d次), %ds后重试: %s", attempt + 1, wait, e)
                    if self._cancellable_sleep(wait):
                        raise AIError("用户取消") from e
                    attempt += 1
                    continue
                if isinstance(e, urllib.error.URLError):
                    raise AIError(f"网络连接失败: {e.reason}", kind="transient") from e
                if isinstance(e, TimeoutError):
                    raise AIError("请求超时，请检查网络连接或 API 地址", kind="transient") from None
                raise AIError(f"请求异常: {e}", kind="unknown") from e
        if resp is None:
            raise AIError(f"网络连接失败 (重试{max_retries}次后仍失败): {last_exc}", kind="transient") from last_exc


        return resp
