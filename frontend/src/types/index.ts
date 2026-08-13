export interface Document {
  id: number;
  filename: string;
  file_path: string;
  file_type: string;
  size: number;
  chunk_count: number;
  created_at: string;
  // #47：封面图片相对路径（如 ``covers/123.png``）；存量/无封面记录为 null。
  cover_image_path?: string | null;
  // #53：小说名（管理端表单必填）；存量/缺省记录为 null，展示层回退 filename。
  title?: string | null;
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

export interface SettingsResponse {
  llm: LLMSettings;
}

export interface SettingsUpdate {
  llm_provider?: string;
  llm_api_key?: string;
  llm_base_url?: string;
  llm_model?: string;
}

// #45：聊天页 preflight 用的 LLM 可用性。
export interface LLMStatus {
  provider: string;
  configured: boolean;
  reason: string;
}

// 会话（#34 / #35）
export interface Conversation {
  id: number;
  title: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationCreate {
  title?: string;
}

export interface ConversationUpdate {
  title?: string;
}