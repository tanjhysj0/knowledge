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
  // #62/#63：处理状态与进度（0-100）。上传落库即 pending/0，后台索引
  // 完成后 ready/100，失败则为 failed。
  status: 'pending' | 'processing' | 'ready' | 'failed';
  progress: number;
  // #63：索引处理失败原因；成功/存量记录为 null。
  error_message?: string | null;
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
  // #36/#58：目标会话 id。后端 Pydantic 强制必填（缺省 422），
  // 前端发送时始终携带；类型上允许缺省以兼容早期调用点。
  conversation_id?: number;
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
  // #52：会话归属的客户端标识（后端透出；前端不直接使用）。
  client_id: string;
  // #52：绑定的小说 id；null = 未绑定小说的通用会话。
  document_id: number | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationCreate {
  title?: string;
  // #52：可选绑定小说 id；同 (client_id, document_id) 幂等返回既有会话。
  document_id?: number;
}

export interface ConversationUpdate {
  title?: string;
}