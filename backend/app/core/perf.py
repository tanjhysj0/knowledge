"""性能计时小工具：统一毫秒口径（``time.perf_counter`` 单调时钟）。

聊天/检索链路的耗时打点（``[perf]`` 前缀日志）共用此模块，避免各处
重复 ``(time.perf_counter() - start) * 1000`` 的换算。
"""
import time


def elapsed_ms(start: float) -> float:
    """自 ``start``（``time.perf_counter()`` 时刻）起经过的毫秒数。"""
    return (time.perf_counter() - start) * 1000.0
