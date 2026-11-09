"""只读本地模型元数据，验证组实际向客户端公布的上下文。"""

from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import urlopen

from .magpie import MagpieError


def context_windows() -> dict[str, int]:
    """读取模型列表中的组容量；仅访问元数据，不读取凭据或调用模型。"""
    address = os.environ.get("MAGPIE_MODELS_URL", "http://127.0.0.1:3425/v1/models")
    try:
        with urlopen(address, timeout=10) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise MagpieError("模型元数据超过 8MB，拒绝截断解析。")
        raw = json.loads(body)
    except (OSError, URLError, ValueError) as error:
        raise MagpieError("无法读取模型上下文元数据；请检查 MAGPIE_MODELS_URL 和本地网关。") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
        raise MagpieError("模型上下文元数据缺少 data 列表。")
    result = {}
    for item in raw["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise MagpieError("模型上下文元数据包含无效条目。")
        reference = item["id"]
        if not reference.startswith("group/"):
            continue
        window = item.get("context_window", item.get("context_length"))
        if type(window) is not int or window <= 0 or reference in result:
            raise MagpieError(f"{reference} 的公布上下文无效或重复。")
        result[reference] = window
    if not result:
        raise MagpieError("模型元数据中没有路由组，拒绝把空目录当成配置。")
    return result
