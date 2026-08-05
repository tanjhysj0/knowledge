from typing import List
import re


class TextChunker:
    """Split text into overlapping chunks for embedding."""

    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str) -> List[str]:
        """Split text into overlapping chunks."""
        if not text or not text.strip():
            return []

        # Clean text
        text = self._clean_text(text)
        
        # Split by sentences/paragraphs first to preserve semantic units
        segments = self._split_segments(text)
        
        chunks = []
        current_chunk = []
        current_length = 0

        for segment in segments:
            segment_length = len(segment)
            
            # If single segment exceeds chunk_size, split it further
            if segment_length > self.chunk_size:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_length = 0
                
                # Split long segment by sentences
                sub_chunks = self._split_long_segment(segment)
                chunks.extend(sub_chunks)
                continue

            # Check if adding this segment would exceed chunk_size
            if current_length + segment_length + 1 > self.chunk_size:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                
                # Start new chunk with overlap
                if self.overlap > 0 and current_chunk:
                    overlap_text = " ".join(current_chunk)
                    if len(overlap_text) >= self.overlap:
                        overlap_start = max(0, len(overlap_text) - self.overlap)
                        overlap_text = overlap_text[overlap_start:]
                        current_chunk = [overlap_text]
                        current_length = len(overlap_text)
                    else:
                        current_chunk = []
                        current_length = 0
                else:
                    current_chunk = []
                    current_length = 0

            current_chunk.append(segment)
            current_length += segment_length + 1

        # Add remaining chunk
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return [c.strip() for c in chunks if c.strip()]

    def _clean_text(self, text: str) -> str:
        """Clean and normalize text."""
        # Replace multiple whitespace with single space
        text = re.sub(r'\s+', ' ', text)
        # Remove leading/trailing whitespace per line and normalize
        lines = text.split('\n')
        lines = [line.strip() for line in lines]
        return '\n'.join(lines)

    def _split_segments(self, text: str) -> List[str]:
        """Split text into semantic segments (paragraphs or sentences)."""
        # Split by double newlines (paragraphs)
        segments = re.split(r'\n\s*\n', text)
        
        result = []
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            
            # If paragraph is short enough, keep it as is
            if len(segment) <= self.chunk_size:
                result.append(segment)
            else:
                # Split long paragraph by sentences
                sentences = self._split_by_sentences(segment)
                result.extend(sentences)
        
        return result

    def _split_by_sentences(self, text: str) -> List[str]:
        """Split text by sentence boundaries."""
        # Common sentence enders
        sentence_pattern = r'(?<=[。！？.!?])\s+'
        sentences = re.split(sentence_pattern, text)
        return [s.strip() for s in sentences if s.strip()]

    def _split_long_segment(self, segment: str) -> List[str]:
        """Split a segment that exceeds chunk_size by character count."""
        chunks = []
        start = 0
        while start < len(segment):
            end = start + self.chunk_size
            chunk = segment[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end - self.overlap if self.overlap > 0 else end
        
        return [c for c in chunks if c]
