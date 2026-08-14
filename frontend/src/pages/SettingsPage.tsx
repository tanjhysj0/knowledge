import { useCallback, useEffect, useMemo, useState } from 'react';
import { modelsApi } from '../services/api';
import type { LLMModel } from '../types';

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
];

// #69：表单初始状态（新增模式）。
const EMPTY_FORM = {
  id: null as number | null,
  provider_type: 'openai' as 'openai' | 'anthropic',
  model_name: '',
  base_url: '',
  api_key: '',
  is_default: false,
};

export default function SettingsPage() {
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // 新增/编辑表单状态；``id`` 非空表示编辑既有记录。
  const [form, setForm] = useState(EMPTY_FORM);
  // 拉取端点返回的模型名选项（两个单选下拉的第二个）。
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const loadModels = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setModels(await modelsApi.list());
    } catch (err) {
      setError('加载模型列表失败，请检查后端服务是否运行');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  // 模型名下拉的选项：拉取结果 + 当前表单值兜底（编辑既有记录时保证可选）。
  const selectableModels = useMemo(() => {
    const options = [...modelOptions];
    if (form.model_name && !options.includes(form.model_name)) {
      options.unshift(form.model_name);
    }
    return options;
  }, [modelOptions, form.model_name]);

  // 列表为空时新增必须设为默认（后端同样兜底拒绝）。
  const forceDefault = models.length === 0 && form.id === null;

  const fetchModelList = async (provider: string, baseUrl: string, apiKey: string) => {
    setFetchingModels(true);
    setFetchError(null);
    try {
      const response = await modelsApi.fetchList({
        provider_type: provider as 'openai' | 'anthropic',
        base_url: baseUrl,
        api_key: apiKey,
      });
      setModelOptions(response.models);
      if (response.models.length === 0) {
        setFetchError('该接口未返回模型，可手动输入模型名');
      }
    } catch (err) {
      setModelOptions([]);
      setFetchError('拉取模型列表失败，可手动输入模型名');
      console.error(err);
    } finally {
      setFetchingModels(false);
    }
  };

  const startEdit = (model: LLMModel) => {
    setForm({
      id: model.id,
      provider_type: model.provider_type,
      model_name: model.model_name,
      base_url: model.base_url,
      api_key: '',
      is_default: model.is_default,
    });
    setFetchError(null);
    setSuccess(null);
    // 编辑时预填当前模型名，避免拉取失败导致下拉无法选中。
    setModelOptions([model.model_name]);
    void fetchModelList(model.provider_type, model.base_url, '');
  };

  const handleSave = async () => {
    if (!form.model_name.trim()) {
      setError('模型名称不能为空');
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      if (form.id === null) {
        await modelsApi.create({
          provider_type: form.provider_type,
          base_url: form.base_url,
          model_name: form.model_name,
          api_key: form.api_key,
          is_default: form.is_default || forceDefault,
        });
        setSuccess('模型已新增');
      } else {
        const payload: {
          provider_type: 'openai' | 'anthropic';
          base_url: string;
          model_name: string;
          api_key?: string;
        } = {
          provider_type: form.provider_type,
          base_url: form.base_url,
          model_name: form.model_name,
        };
        // api_key 留空 = 保持原值（与后端语义一致）。
        if (form.api_key) {
          payload.api_key = form.api_key;
        }
        await modelsApi.update(form.id, payload);
        setSuccess('模型已更新');
      }
      setForm(EMPTY_FORM);
      setModelOptions([]);
      await loadModels();
    } catch (err) {
      setError('保存失败，请检查填写内容');
      console.error(err);
    } finally {
      setSaving(false);
    }
  };

  const handleSetDefault = async (model: LLMModel) => {
    try {
      setError(null);
      setSuccess(null);
      await modelsApi.setDefault(model.id);
      setSuccess(`已将 ${model.model_name} 设为默认`);
      await loadModels();
    } catch (err) {
      setError('设置默认模型失败');
      console.error(err);
    }
  };

  const handleDelete = async (model: LLMModel) => {
    if (!window.confirm(`确定删除模型 ${model.model_name} 吗？`)) {
      return;
    }
    try {
      setError(null);
      setSuccess(null);
      await modelsApi.remove(model.id);
      setSuccess('模型已删除');
      await loadModels();
    } catch (err) {
      setError('删除失败：默认模型需先将其它模型设为默认');
      console.error(err);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-6">模型管理</h1>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {success && (
        <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-lg text-green-700">
          {success}
        </div>
      )}

      {/* 模型列表表格 */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-gray-600">
              <th className="px-4 py-3">接口类型</th>
              <th className="px-4 py-3">模型名称</th>
              <th className="px-4 py-3">Base URL</th>
              <th className="px-4 py-3">API Key</th>
              <th className="px-4 py-3">默认</th>
              <th className="px-4 py-3">操作</th>
            </tr>
          </thead>
          <tbody>
            {models.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  暂无模型，请新增第一条（将自动设为默认）
                </td>
              </tr>
            ) : (
              models.map((model) => (
                <tr key={model.id} className="border-t border-gray-100">
                  <td className="px-4 py-3">
                    {PROVIDER_OPTIONS.find((p) => p.value === model.provider_type)?.label ||
                      model.provider_type}
                  </td>
                  <td className="px-4 py-3">{model.model_name}</td>
                  <td className="px-4 py-3 text-gray-500 truncate max-w-[200px]">
                    {model.base_url || '-'}
                  </td>
                  <td className="px-4 py-3 text-gray-500">{model.api_key_masked || '-'}</td>
                  <td className="px-4 py-3">
                    {model.is_default ? (
                      <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded-full text-xs">
                        默认
                      </span>
                    ) : (
                      <button
                        onClick={() => void handleSetDefault(model)}
                        className="text-blue-600 hover:underline"
                      >
                        设默认
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <button
                      onClick={() => startEdit(model)}
                      className="text-blue-600 hover:underline mr-3"
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => void handleDelete(model)}
                      className="text-red-600 hover:underline"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* 新增 / 编辑表单 */}
      <div className="bg-white rounded-lg shadow p-6 mt-6">
        <h3 className="text-lg font-semibold mb-4">
          {form.id === null ? '新增模型' : '编辑模型'}
        </h3>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">接口类型</label>
            <select
              value={form.provider_type}
              onChange={(e) => {
                const provider = e.target.value as 'openai' | 'anthropic';
                setForm((prev) => ({ ...prev, provider_type: provider, model_name: '' }));
                setModelOptions([]);
                setFetchError(null);
              }}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              {PROVIDER_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
            <input
              type="password"
              value={form.api_key}
              placeholder={
                form.id !== null && models.find((m) => m.id === form.id)?.api_key_masked
                  ? `当前: ${models.find((m) => m.id === form.id)?.api_key_masked}（留空保持原值）`
                  : '输入 API Key'
              }
              onChange={(e) => setForm((prev) => ({ ...prev, api_key: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
            <input
              type="text"
              value={form.base_url}
              onChange={(e) => setForm((prev) => ({ ...prev, base_url: e.target.value }))}
              placeholder="如 https://api.openai.com/v1"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
            <select
              value={form.model_name}
              onChange={(e) => setForm((prev) => ({ ...prev, model_name: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              <option value="">请选择模型</option>
              {selectableModels.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <div className="mt-2 flex items-center gap-3">
              <button
                type="button"
                onClick={() => void fetchModelList(form.provider_type, form.base_url, form.api_key)}
                disabled={fetchingModels}
                className="px-3 py-1.5 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 disabled:opacity-50 text-sm"
              >
                {fetchingModels ? '拉取中...' : '拉取模型列表'}
              </button>
              {fetchError && <span className="text-xs text-gray-500">{fetchError}</span>}
              {!fetchError && modelOptions.length > 0 && (
                <span className="text-xs text-gray-500">共 {modelOptions.length} 个模型</span>
              )}
            </div>
          </div>

          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
              <input
                type="checkbox"
                checked={form.is_default || forceDefault}
                disabled={forceDefault || saving}
                onChange={(e) => setForm((prev) => ({ ...prev, is_default: e.target.checked }))}
              />
              设为默认模型
              {forceDefault && <span className="text-xs text-gray-400">（列表为空，自动默认）</span>}
            </label>
          </div>
        </div>

        <div className="mt-6 flex gap-4">
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? '保存中...' : form.id === null ? '新增' : '保存修改'}
          </button>
          <button
            onClick={() => {
              setForm(EMPTY_FORM);
              setModelOptions([]);
              setFetchError(null);
              setSuccess(null);
            }}
            disabled={saving}
            className="px-6 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 disabled:opacity-50"
          >
            重置
          </button>
        </div>
      </div>
    </div>
  );
}
