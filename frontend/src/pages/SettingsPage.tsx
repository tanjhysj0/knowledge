import { useState, useEffect } from 'react';
import { settingsApi } from '../services/api';
import type { SettingsUpdate } from '../types';

const LLM_PROVIDERS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
];

interface ProviderConfigProps {
  title: string;
  provider: string;
  providers: { value: string; label: string }[];
  apiKeyMasked: string;
  baseUrl: string;
  model: string;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
}

function ProviderConfig({
  title,
  provider,
  providers,
  apiKeyMasked,
  baseUrl,
  model,
  onProviderChange,
  onApiKeyChange,
  onBaseUrlChange,
  onModelChange,
}: ProviderConfigProps) {
  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-lg font-semibold mb-4">{title}</h3>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
          <select
            value={provider}
            onChange={(e) => onProviderChange(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          >
            {providers.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
          <input
            type="password"
            placeholder={apiKeyMasked || '输入新 API Key 覆盖当前值'}
            onChange={(e) => onApiKeyChange(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
          {apiKeyMasked && (
            <p className="text-xs text-gray-500 mt-1">当前: {apiKeyMasked}</p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => onBaseUrlChange(e.target.value)}
            placeholder="https://api.openai.com/v1"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
          <input
            type="text"
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder="gpt-4o-mini"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [llmProvider, setLlmProvider] = useState('openai');
  const [llmApiKeyMasked, setLlmApiKeyMasked] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmBaseUrl, setLlmBaseUrl] = useState('');
  const [llmModel, setLlmModel] = useState('');

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      setLoading(true);
      setError(null);
      const settings = await settingsApi.get();
      setLlmProvider(settings.llm.provider);
      setLlmApiKeyMasked(settings.llm.api_key_masked);
      setLlmBaseUrl(settings.llm.base_url);
      setLlmModel(settings.llm.model);
    } catch (err) {
      setError('加载配置失败，请检查后端服务是否运行');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);

      const update: SettingsUpdate = {
        llm_provider: llmProvider,
        llm_base_url: llmBaseUrl,
        llm_model: llmModel,
      };

      if (llmApiKey) {
        update.llm_api_key = llmApiKey;
      }

      await settingsApi.update(update);

      setLlmApiKey('');
      setSuccess('配置已保存并生效');
    } catch (err) {
      setError('保存配置失败，请检查 API Key 和网络连接');
      console.error(err);
    } finally {
      setSaving(false);
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
      <h1 className="text-2xl font-bold mb-6">设置</h1>

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

      <div className="space-y-6">
        <ProviderConfig
          title="LLM 配置"
          provider={llmProvider}
          providers={LLM_PROVIDERS}
          apiKeyMasked={llmApiKeyMasked}
          baseUrl={llmBaseUrl}
          model={llmModel}
          onProviderChange={setLlmProvider}
          onApiKeyChange={setLlmApiKey}
          onBaseUrlChange={setLlmBaseUrl}
          onModelChange={setLlmModel}
        />
      </div>

      <div className="mt-6 flex gap-4">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
        <button
          onClick={loadSettings}
          disabled={loading || saving}
          className="px-6 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 disabled:opacity-50"
        >
          重置
        </button>
      </div>
    </div>
  );
}