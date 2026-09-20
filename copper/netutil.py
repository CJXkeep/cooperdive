"""网络工具：系统代理处理、带重试的 HTTP 会话、akshare 调用重试。"""

from __future__ import annotations

import time
import urllib.request

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from copper import config

if config.DISABLE_SYSTEM_PROXY:
    # Windows 下 requests 会读取注册表代理（getproxies_registry），本地开发时干扰明显；
    # 统一屏蔽后全部直连。服务器上无系统代理，此补丁无副作用。
    urllib.request.getproxies = lambda: {}
    urllib.request.getproxies_registry = lambda: {}

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update(UA)
        _session = s
    return _session


def http_get(url: str, timeout: int = 20, **kwargs) -> requests.Response:
    """带重试的 GET。"""
    kwargs.setdefault("timeout", timeout)
    return session().get(url, **kwargs)


def fetch_text(url: str, encoding: str = "utf-8", timeout: int = 20) -> str:
    r = http_get(url, timeout=timeout)
    r.raise_for_status()
    r.encoding = encoding
    return r.text


def call_with_retry(fn, *args, tries: int = 3, pause: float = 6.0, **kwargs):
    """akshare 接口没有内建重试，统一包一层。失败抛最后一次异常。"""
    last_exc: Exception | None = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 采集层需要吞掉一切网络类错误后重试
            last_exc = exc
            if i < tries - 1:
                time.sleep(pause * (i + 1))
    assert last_exc is not None
    raise last_exc
