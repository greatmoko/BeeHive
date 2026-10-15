"""搜索和 HTTP 请求脚本模板。"""
from __future__ import annotations
import json
from beehive.features.wokbee.pipeline.script_templates import _COMMON_HEADER, _hdr
def _script_web_search(query: str, max_results: int = 5) -> str:
    q = json.dumps(query, ensure_ascii=False)
    mr = int(max_results)
    return (
        _hdr(_COMMON_HEADER)
        + f"""
QUERY = {q}
MAX_RESULTS = {mr}

def main() -> None:
    url = f"https://html.duckduckgo.com/html/?q={{quote_plus(QUERY)}}"
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={{"User-Agent": "Mozilla/5.0 (compatible; WokBeeScript/0.1)"}},
        ) as client:
            html = _resp_text(client.get(url))
    except Exception as e:
        _save(f"脚本执行失败：搜索失败：{{e}}")
        sys.exit(1)
    results = []
    blocks = re.findall(
        r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
    )
    for href, title in blocks[:MAX_RESULTS]:
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = unquote(m.group(1))
        results.append(f"- {{_strip_html(title)[:120]}}\\n  URL: {{href}}")
    if not results:
        _save("未找到搜索结果")
        return
    _save(f"搜索「{{QUERY}}」结果：\\n" + "\\n".join(results))

if __name__ == "__main__":
    main()
"""
    )

def _script_http_get(url: str, max_chars: int = 12000) -> str:
    u = json.dumps(url, ensure_ascii=False)
    mc = int(max_chars)
    return (
        _hdr(_COMMON_HEADER)
        + f"""
URL = {u}
MAX_CHARS = {mc}

def main() -> None:
    try:
        with httpx.Client(
            timeout=45.0,
            follow_redirects=True,
            headers={{
                "User-Agent": "Mozilla/5.0 (compatible; WokBeeScript/0.1)",
                "Accept": "text/html,application/json,text/plain,*/*",
            }},
        ) as client:
            resp = client.get(URL)
            ctype = (resp.headers.get("content-type") or "").lower()
            text = _resp_text(resp)
            if "application/json" in ctype:
                try:
                    text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
                except Exception:
                    pass
            elif "html" in ctype:
                text = _strip_html(text)
            body = text[:MAX_CHARS]
            header = f"HTTP {{resp.status_code}} | {{ctype or 'unknown'}} | len={{len(text)}}"
            _save(f"{{header}}\\nURL: {{resp.url}}\\n\\n{{body}}")
    except Exception as e:
        _save(f"脚本执行失败：请求失败：{{e}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
"""
    )

def _script_http_request(
    url: str,
    method: str = "GET",
    headers_json: str = "",
    body: str = "",
    max_chars: int = 12000,
) -> str:
    return (
        _hdr(_COMMON_HEADER)
        + f"""
URL = {json.dumps(url, ensure_ascii=False)}
METHOD = {json.dumps(method, ensure_ascii=False)}
HEADERS_JSON = {json.dumps(headers_json, ensure_ascii=False)}
BODY = {json.dumps(body, ensure_ascii=False)}
MAX_CHARS = {int(max_chars)}

def main() -> None:
    headers = {{"User-Agent": "Mozilla/5.0 (compatible; WokBeeScript/0.1)"}}
    if HEADERS_JSON.strip():
        try:
            extra = json.loads(HEADERS_JSON)
            if isinstance(extra, dict):
                headers.update({{str(k): str(v) for k, v in extra.items()}})
        except json.JSONDecodeError as e:
            _save(f"脚本执行失败：headers_json 非法：{{e}}")
            sys.exit(1)
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.request(
                METHOD,
                URL,
                headers=headers,
                content=BODY.encode("utf-8") if BODY else None,
            )
            ctype = (resp.headers.get("content-type") or "").lower()
            text = _resp_text(resp)
            if "json" in ctype:
                try:
                    text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
                except Exception:
                    pass
            elif "html" in ctype:
                text = _strip_html(text)
            _save(
                f"HTTP {{resp.status_code}} {{METHOD}} | {{ctype or 'unknown'}}\\n"
                f"URL: {{resp.url}}\\n\\n{{text[:MAX_CHARS]}}"
            )
    except Exception as e:
        _save(f"脚本执行失败：请求失败：{{e}}")
        sys.exit(1)

if __name__ == "__main__":
    main()
"""
    )
