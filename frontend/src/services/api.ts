import axios from 'axios';
import type { Document, ChatMessage, ChatRequest, PaginatedDocumentsResponse, UploadProgress, SettingsResponse, SettingsUpdate } from '../types';

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

  history: async (): Promise<ChatMessage[]> => {
    const response = await api.get<ChatMessage[]>('/chat/history');
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
