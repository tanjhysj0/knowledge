"""#69：运行时默认模型单例。

``llm_models`` 表是配置事实源，但 provider 构造与 preflight 是同步热路径，
不应每次读库。启动时把**默认模型记录**加载进内存单例；模型 CRUD / 设默认
/ ``PUT /api/settings`` 等写路径在提交成功后重新同步本单例并重建 provider
实例，保证「修改默认模型后下一次对话生效」而无需重启进程。

单例只承载当前生效的一条默认记录（provider_type / base_url / model_name /
api_key）；无默认记录（列表为空或启动加载失败）时为未配置空态。
"""
from dataclasses import dataclass


@dataclass
class RuntimeModelConfig:
    """当前生效的默认模型配置（与 ``llm_models`` 默认行一一对应）。"""

    provider_type: str = "openai"
    base_url: str = ""
    model_name: str = ""
    api_key: str = ""

    def is_configured(self) -> bool:
        """api_key 与 model_name 均非空才算已配置（与 preflight 语义一致）。"""
        return bool(self.api_key and self.api_key.strip()) and bool(
            self.model_name and self.model_name.strip()
        )


_runtime_model = RuntimeModelConfig()


def get_runtime_model() -> RuntimeModelConfig:
    """读取当前生效的默认模型配置。"""
    return _runtime_model


def set_runtime_model(config: RuntimeModelConfig) -> None:
    """整体替换运行时默认模型配置（写路径提交成功后调用）。"""
    global _runtime_model
    _runtime_model = config


def reset_runtime_model() -> None:
    """清空为未配置空态（无默认记录 / 加载失败时调用）。"""
    set_runtime_model(RuntimeModelConfig())
