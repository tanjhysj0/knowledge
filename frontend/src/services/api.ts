import axios from 'axios';
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
} from '../types';

const api = axios.create({
  baseURL: '/api',
});

export const documentApi = {
  upload: async (
    file: File,
    onProgress?: (progress: UploadProgress) => void
  ): Promise<Document> => {
    const formData = new FormData();
    formData.append('file', file);
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

  list: async (page: number = 1, pageSize: number = 10): Promise<PaginatedDocumentsResponse> => {
    const response = await api.get<PaginatedDocumentsResponse>('/documents', {
      params: { page, page_size: pageSize },
    });
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await api.delete(`/documents/${id}`);
  },
};

export const chatApi = {
  stream: async (request: ChatRequest): Promise<ReadableStream> => {
    const response = await api.post('/chat/stream', request, {
      responseType: 'stream',
    });
    return response.data;
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

// #45：聊天页 preflight 用的 LLM 可用性查询。
export const llmStatusApi = {
  get: async (): Promise<LLMStatus> => {
    const response = await api.get<LLMStatus>('/llm/status');
    return response.data;
  },
};
