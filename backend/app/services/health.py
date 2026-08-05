"""系统状态应用服务。"""


def get_health_status() -> dict[str, str]:
    """返回服务健康状态。"""
    return {"status": "healthy"}
