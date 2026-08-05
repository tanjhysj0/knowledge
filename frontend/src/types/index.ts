export interface Document {
  id: number;
  filename: string;
  file_path: string;
  file_type: string;
  size: number;
  chunk_count: number;
  created_at: string;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  document_ids?: string;
  created_at: string;
}

export interface ChatRequest {
  message: string;
  document_ids: number[];
}

export interface ChatResponse {
  message: string;
  sources: string[];
}

export interface PaginatedDocumentsResponse {
  items: Document[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface UploadProgress {
  loaded: number;
  total: number;
  percentage: number;
}

export interface LLMSettings {
  provider: string;
  api_key_masked: string;
  base_url: string;
  model: string;
}

export interface EmbeddingSettings {
  provider: string;
  api_key_masked: string;
  base_url: string;
  model: string;
}

export interface SettingsResponse {
  llm: LLMSettings;
  embedding: EmbeddingSettings;
}

export interface SettingsUpdate {
  llm_provider?: string;
  llm_api_key?: string;
  llm_base_url?: string;
  llm_model?: string;
  embedding_provider?: string;
  embedding_api_key?: string;
  embedding_base_url?: string;
  embedding_model?: string;
}
