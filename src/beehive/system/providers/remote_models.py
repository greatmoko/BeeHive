"""查询厂商模型列表，不读写本地配置。"""
import json
import urllib.request
import urllib.error
from beehive.common.errors import AIError


def fetch_remote_models(endpoint: str, api_key: str) -> list[str]:
    url = endpoint.rstrip("/")
    for suffix in ["/chat/completions", "/completions"]:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    url = url.rstrip("/")
    if not url.endswith("/models"):
        url += "/models"

    req = urllib.request.Request(url, method="GET")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise AIError(f"HTTP {e.code}: {body[:200]}") from e
    except urllib.error.URLError as e:
        raise AIError(f"网络错误: {e.reason}") from e
    except Exception as e:
        raise AIError(f"请求异常: {e}") from e

    models: list[str] = []
    if isinstance(data, dict) and "data" in data:
        for item in data["data"]:
            mid = item.get("id", "")
            if mid:
                models.append(mid)
    models.sort()
    return models
