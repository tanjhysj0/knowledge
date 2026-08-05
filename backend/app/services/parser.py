import pdfplumber
from docx import Document
from typing import Optional
import re


class DocumentParser:
    """Parse different document formats into plain text."""

    @staticmethod
    def parse(file_path: str, file_ext: str) -> str:
        """Parse document based on file extension."""
        parsers = {
            "txt": DocumentParser._parse_txt,
            "md": DocumentParser._parse_markdown,
            "pdf": DocumentParser._parse_pdf,
            "docx": DocumentParser._parse_docx,
        }
        
        parser = parsers.get(file_ext.lower())
        if not parser:
            raise ValueError(f"Unsupported file type: {file_ext}")
        
        return parser(file_path)

    @staticmethod
    def _parse_txt(file_path: str) -> str:
        """Parse plain text file."""
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _parse_markdown(file_path: str) -> str:
        """Parse markdown file, treating it as plain text with markdown stripped."""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Basic markdown cleanup - remove common markdown syntax
        content = re.sub(r'```[\s\S]*?```', '', content)  # Remove code blocks
        content = re.sub(r'`[^`]+`', '', content)  # Remove inline code
        content = re.sub(r'!\[.*?\]\(.*?\)', '', content)  # Remove images
        content = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', content)  # Links -> text
        content = re.sub(r'[#*_~>-]+', '', content)  # Remove markdown symbols
        content = re.sub(r'\n{3,}', '\n\n', content)  # Normalize newlines
        return content.strip()

    @staticmethod
    def _parse_pdf(file_path: str) -> str:
        """Extract text from PDF using pdfplumber."""
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)

    @staticmethod
    def _parse_docx(file_path: str) -> str:
        """Extract text from DOCX using python-docx."""
        doc = Document(file_path)
        paragraphs = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                paragraphs.append(text)
        return "\n\n".join(paragraphs)
