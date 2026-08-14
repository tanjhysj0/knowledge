import axios from 'axios';
import { getClientId } from '../utils/clientId';
import type {
  Document,
  ChatMessage,
  ChatRequest,
  Conversation,
  ConversationCreate,
  ConversationUpdate,
  LLMStatus,
  PaginatedDocumentsResponse,
  UploadProgress,
  SettingsResponse,
  SettingsUpdate,
  LLMModel,
  LLMModelCreate,
  LLMModelUpdate,
  ModelListFetchRequest,
  ModelListResponse,
} from '../types';

const api = axios.create({
  baseURL: '/api',
});

// #52：所有请求统一携带 X-Client-Id（首次访问生成并持久化到
// localStorage），后端据此按浏览器（客户端）隔离会话空间。
api.interceptors.request.use((config) => {
  config.headers['X-Client-Id'] = getClientId();
  return config;
});

/** #45/#58：LLM 不可用时由服务层抛出（503 preflight 拒绝 / 运行时失败），
 * 调用方据此改走 banner 显示而非通用错误。 */
export class LLMUnavailableError extends Error {
  readonly showSettingsLink: boolean;
  constructor(message: string, showSettingsLink: boolean) {
    super(message);
    this.name = 'LLMUnavailableError';
    this.showSettingsLink = showSettingsLink;
  }
}

export const documentApi = {
  upload: async (
    file: File,
    cover: File | null = null,
    title?: string,
    onProgress?: (progress: UploadProgress) => void
  ): Promise<Document> => {
    const formData = new FormData();
    formData.append('file', file);
    // #48：可选封面（封面字段与后端 multipart 字段名一致）
    if (cover) {
      formData.append('cover', cover);
    }
    // #53：小说名（管理端表单必填；缺省时后端回退文件名去扩展名）
    if (title) {
      formData.append('title', title);
    }
    const response = await api.post<Document>('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.total) {
          onProgress({
            loaded: progressEvent.loaded,
            total: progressEvent.total,
            percentage: Math.round((progressEvent.loaded * 100) / progressEvent.total),
          });
        }
      },
    });
    return response.data;
  },

  // #63：``allStatuses`` 为 true 时返回全量视图（管理端）；默认仅 ready（前台书架）。
  list: async (
    page: number = 1,
    pageSize: number = 10,
    allStatuses: boolean = false
  ): Promise<PaginatedDocumentsResponse> => {
    const response = await api.get<PaginatedDocumentsResponse>('/documents', {
      params: { page, page_size: pageSize, all_statuses: allStatuses },
    });
    return response.data;
  },

  // 单文档详情：管理端编辑页按 id 拉取预填数据（刷新可恢复）。
  get: async (id: number): Promise<Document> => {
    const response = await api.get<Document>(`/documents/${id}`);
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await api.delete(`/documents/${id}`);
  },

  // #53：编辑小说——仅改小说名与换封面，正文不可换。
  update: async (
    id: number,
    payload: { title?: string; cover?: File | null }
  ): Promise<Document> => {
    const formData = new FormData();
    if (payload.title !== undefined) {
      formData.append('title', payload.title);
    }
    if (payload.cover) {
      formData.append('cover', payload.cover);
    }
    const response = await api.patch<Document>(`/documents/${id}`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  // #65：重试索引——failed 小说重置 pending 并重新入队后台处理。
  reindex: async (id: number): Promise<Document> => {
    const response = await api.post<Document>(`/documents/${id}/reindex`);
    return response.data;
  },
};

export const chatApi = {
  /**
   * #58：聊天流式请求统一走服务层。浏览器端 axios（XHR adapter）不支持
   * ``responseType: 'stream'``（会静默退化为整段文本），故服务层内部用
   * fetch 发出请求、把响应流透传给调用方；X-Client-Id 与 axios 拦截器
   * 保持一致。503（LLM 未配置 preflight 拒绝）在此解析 reason 并抛
   * LLMUnavailableError，其余非 2xx 由调用方走通用错误。
   */
  stream: async (
    request: ChatRequest,
    signal?: AbortSignal
  ): Promise<Response> => {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': getClientId(),
      },
      body: JSON.stringify(request),
      signal,
    });
    if (response.status === 503) {
      // #45 后端 preflight 拒绝：提取 reason 后改走 banner。
      let body: { reason?: string; error?: string } = {};
      try {
        body = (await response.json()) as { reason?: string; error?: string };
      } catch {
        // 后端未返回合法 JSON 时使用兜底文案。
      }
      throw new LLMUnavailableError(
        body.reason || body.error || 'LLM 不可用',
        true
      );
    }
    return response;
  },
};

export const conversationApi = {
  list: async (): Promise<Conversation[]> => {
    const response = await api.get<Conversation[]>('/conversations');
    return response.data;
  },
  create: async (payload: ConversationCreate = {}): Promise<Conversation> => {
    const response = await api.post<Conversation>('/conversations', payload);
    return response.data;
  },
  remove: async (id: number): Promise<void> => {
    await api.delete(`/conversations/${id}`);
  },
  messages: async (id: number): Promise<ChatMessage[]> => {
    const response = await api.get<ChatMessage[]>(`/conversations/${id}/messages`);
    return response.data;
  },
  update: async (id: number, payload: ConversationUpdate): Promise<Conversation> => {
    const response = await api.patch<Conversation>(
      `/conversations/${id}`,
      payload
    );
    return response.data;
  },
};

export const settingsApi = {
  get: async (): Promise<SettingsResponse> => {
    const response = await api.get<SettingsResponse>('/settings');
    return response.data;
  },

  update: async (settings: SettingsUpdate): Promise<SettingsResponse> => {
    const response = await api.put<SettingsResponse>('/settings', settings);
    return response.data;
  },
};

// #68/#69：模型列表 CRUD + 模型列表拉取代理。
export const modelsApi = {
  list: async (): Promise<LLMModel[]> => {
    const response = await api.get<LLMModel[]>('/models');
    return response.data;
  },

  create: async (payload: LLMModelCreate): Promise<LLMModel> => {
    const response = await api.post<LLMModel>('/models', payload);
    return response.data;
  },

  update: async (id: number, payload: LLMModelUpdate): Promise<LLMModel> => {
    const response = await api.put<LLMModel>(`/models/${id}`, payload);
    return response.data;
  },

  remove: async (id: number): Promise<void> => {
    await api.delete(`/models/${id}`);
  },

  setDefault: async (id: number): Promise<LLMModel> => {
    const response = await api.put<LLMModel>(`/models/${id}/default`);
    return response.data;
  },

  // #69：给定接口类型 + base_url + api_key，后端代理拉取模型名列表。
  fetchList: async (payload: ModelListFetchRequest): Promise<ModelListResponse> => {
    const response = await api.post<ModelListResponse>('/models/fetch', payload);
    return response.data;
  },
};

// #45：聊天页 preflight 用的 LLM 可用性查询。
export const llmStatusApi = {
  get: async (): Promise<LLMStatus> => {
    const response = await api.get<LLMStatus>('/llm/status');
    return response.data;
  },
};
