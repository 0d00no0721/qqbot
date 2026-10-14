# -*- coding: utf-8 -*-
"""commands 包 — 指令注册表化拆分。

导入本包即触发各子模块的 @register 装饰器执行，将指令注册到 _registry。
导入顺序即同优先级指令的派发顺序（先注册先匹配）。
"""

# 基础设施（无副作用，仅定义）
from . import _state  # noqa: F401
from . import _registry  # noqa: F401
from . import _helpers  # noqa: F401

# 功能模块（导入即注册指令）
from . import cipher  # noqa: F401
from . import daily  # noqa: F401
from . import menu  # noqa: F401
from . import yau  # noqa: F401
from . import zhouli  # noqa: F401
from . import pixelart  # noqa: F401
from . import images  # noqa: F401
from . import divination  # noqa: F401
from . import piano  # noqa: F401
from . import character  # noqa: F401
from . import system  # noqa: F401
from . import voice  # noqa: F401
from . import tts  # noqa: F401
from . import help  # noqa: F401
