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
