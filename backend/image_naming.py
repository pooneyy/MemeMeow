"""图片展示名称和保存文件名的纯规则工具。

该模块位于图片领域和持久化模型之间，只处理用户可见名称的规范化、扩展名校验
以及完整保存文件名拼接，不读取数据库或文件系统。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Final


SUPPORTED_IMAGE_EXTENSIONS: Final[tuple[str, ...]] = (".png", ".jpg", ".jpeg", ".gif")
"""服务端支持的图片扩展名，统一使用小写并包含点号。"""

MAX_DISPLAY_NAME_LENGTH: Final[int] = 255
"""展示名称允许的最大 Unicode 字符数。"""

RESERVED_DISPLAY_NAMES: Final[frozenset[str]] = frozenset({".", "..", ".staging", ".quarantine"})
"""不能作为业务名称或内部存储目录标识的保留名称。"""


def normalize_extension(value: object) -> str:
    """校验并规范化图片扩展名。

    参数 `value` 是带点或不带点的客户端/数据库扩展名，返回小写且带点的扩展名；
    调用场景是 Meme 创建、保存文件名拼接和迁移边界。
    """
    if not isinstance(value, str):
        raise ValueError("extension_invalid")
    extension = value.strip().lower()
    if extension and not extension.startswith("."):
        extension = f".{extension}"
    if extension not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("unsupported_format")
    return extension


def normalize_display_name(value: object) -> str:
    """规范化独立的图片展示名称并拒绝不能安全存储的输入。

    输入可以带一个或多个已知图片后缀，后缀会被剥离；返回值不含图片扩展名，且
    不包含路径分隔符、控制字符或首尾空格/点。名称长度以 Unicode 字符计，调用
    场景是上传初始名称、人工重命名、自动命名和数据库模型赋值。
    """
    if not isinstance(value, str):
        raise ValueError("display_name_invalid")
    name = unicodedata.normalize("NFKC", value).strip()
    if not name:
        raise ValueError("display_name_required")
    if "/" in name or "\\" in name or Path(name).name != name:
        raise ValueError("display_name_path_forbidden")
    if any(unicodedata.category(character) == "Cc" or ord(character) == 127 for character in name):
        raise ValueError("display_name_control_character")

    # 允许客户端直接提交保存文件名，但持久化值始终不包含已知图片后缀。
    while True:
        lowered = name.lower()
        matched = next((extension for extension in SUPPORTED_IMAGE_EXTENSIONS if lowered.endswith(extension)), None)
        if matched is None:
            break
        name = name[: -len(matched)].rstrip()
        if not name:
            raise ValueError("display_name_required")

    if name in RESERVED_DISPLAY_NAMES or name.strip(" .") != name:
        raise ValueError("display_name_invalid")
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError("display_name_too_long")
    return name


def saved_filename(display_name: object, extension: object) -> str:
    """拼接完整的用户保存文件名。

    输入是独立展示名称和图片扩展名，输出为 `display_name + extension`；调用场景是
    上传结果、图片列表、任务摘要和合集 manifest 投影，绝不使用物理 storage key。
    """
    return f"{normalize_display_name(display_name)}{normalize_extension(extension)}"


def public_filename_fields(record: object) -> dict[str, str]:
    """从 Meme 记录构造公开名称字段。

    输入是包含 ``display_name`` 和 ``extension`` 的数据库记录，输出包含展示名及
    完整文件名；任一字段缺失或不安全时直接抛出 ``ValueError``，调用方应省略公开
    文件名，而不能回退到内容寻址的物理 key。
    """
    display_name = normalize_display_name(getattr(record, "display_name", None))
    extension = normalize_extension(getattr(record, "extension", None))
    filename = f"{display_name}{extension}"
    return {"display_name": display_name, "filename": filename, "saved_filename": filename}


def content_addressed_key(sha256: object, extension: object) -> str:
    """由最终图片 SHA-256 和规范化扩展名生成不可变物理 key。"""
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
        raise ValueError("sha256_invalid")
    return f"{sha256.lower()}{normalize_extension(extension)}"


def is_content_addressed_key(storage_key: object, sha256: object, extension: object) -> bool:
    """判断数据库物理 key 是否与声明的图片内容身份完全一致。"""
    try:
        return storage_key == content_addressed_key(sha256, extension)
    except ValueError:
        return False


__all__ = [
    "MAX_DISPLAY_NAME_LENGTH",
    "RESERVED_DISPLAY_NAMES",
    "SUPPORTED_IMAGE_EXTENSIONS",
    "normalize_display_name",
    "normalize_extension",
    "saved_filename",
    "public_filename_fields",
    "content_addressed_key",
    "is_content_addressed_key",
]
