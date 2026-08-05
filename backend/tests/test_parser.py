"""Unit tests for DocumentParser."""
import pytest
from unittest.mock import mock_open, patch, MagicMock
from app.services.parser import DocumentParser


class TestDocumentParser:
    """Tests for DocumentParser."""

    def test_parse_unsupported_file_type(self):
        """Test that unsupported file types raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DocumentParser.parse("/path/to/file.xyz", "xyz")
        assert "Unsupported file type" in str(exc_info.value)

    def test_parse_uppercase_extension(self):
        """Test that uppercase extensions are handled correctly."""
        mock_content = "Test content"
        with patch("builtins.open", mock_open(read_data=mock_content)):
            result = DocumentParser.parse("/path/to/file.txt", "TXT")
            assert result == mock_content


class TestDocumentParserText:
    """Tests for plain text file parsing."""

    def test_parse_txt_file(self):
        """Test parsing a plain text file."""
        content = "Hello, this is a test file.\nWith multiple lines."
        with patch("builtins.open", mock_open(read_data=content)) as mock_file:
            result = DocumentParser._parse_txt("/path/to/file.txt")
            mock_file.assert_called_with("/path/to/file.txt", "r", encoding="utf-8")
            assert result == content

    def test_parse_txt_empty_file(self):
        """Test parsing an empty text file."""
        with patch("builtins.open", mock_open(read_data="")):
            result = DocumentParser._parse_txt("/path/to/file.txt")
            assert result == ""

    def test_parse_txt_with_unicode(self):
        """Test parsing text file with Unicode content."""
        content = "你好世界！🌍 测试文档 📄"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_txt("/path/to/file.txt")
            assert result == content


class TestDocumentParserMarkdown:
    """Tests for markdown file parsing."""

    def test_parse_markdown_basic(self):
        """Test parsing a basic markdown file."""
        content = "# Hello\n\nThis is a test."
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_markdown("/path/to/file.md")
            # Markdown headers should be stripped
            assert "#" not in result
            assert "Hello" in result
            assert "This is a test" in result

    def test_parse_markdown_links_converted(self):
        """Test that markdown links are converted to text."""
        content = "[Click here](https://example.com) and [another link](https://test.com)"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_markdown("/path/to/file.md")
            # Links should be converted to their text
            assert "Click here" in result
            assert "another link" in result
            # URLs should be removed
            assert "https://" not in result

    def test_parse_markdown_code_blocks_removed(self):
        """Test that code blocks are removed."""
        content = "# Header\n\n```python\nprint('hello')\n```\n\nMore text"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_markdown("/path/to/file.md")
            # Code blocks should be removed
            assert "```" not in result
            assert "print" not in result
            assert "Header" in result
            assert "More text" in result

    def test_parse_markdown_inline_code_removed(self):
        """Test that inline code is removed."""
        content = "Use `print()` function to output."
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_markdown("/path/to/file.md")
            # Inline code should be removed
            assert "`" not in result
            assert "function to output" in result

    def test_parse_markdown_images_removed(self):
        """Test that markdown images are removed."""
        content = "Text before ![Alt text](image.png) text after"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_markdown("/path/to/file.md")
            # Images should be removed
            assert "![" not in result
            assert "image.png" not in result
            assert "Text before" in result
            assert "text after" in result

    def test_parse_markdown_multiple_newlines_normalized(self):
        """Test that multiple newlines are normalized."""
        content = "Paragraph 1\n\n\n\n\nParagraph 2"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser._parse_markdown("/path/to/file.md")
            # Triple+ newlines should be normalized to double
            assert "\n\n\n" not in result


class TestDocumentParserPDF:
    """Tests for PDF file parsing."""

    def test_parse_pdf_single_page(self):
        """Test parsing a single-page PDF."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page 1 content"

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open_pdf:
            mock_open_pdf.return_value.__enter__.return_value = mock_pdf
            result = DocumentParser._parse_pdf("/path/to/file.pdf")

            assert "Page 1 content" in result
            mock_open_pdf.assert_called_once_with("/path/to/file.pdf")

    def test_parse_pdf_multiple_pages(self):
        """Test parsing a multi-page PDF."""
        mock_pages = []
        for i in range(3):
            page = MagicMock()
            page.extract_text.return_value = f"Page {i + 1} content"
            mock_pages.append(page)

        mock_pdf = MagicMock()
        mock_pdf.pages = mock_pages

        with patch("pdfplumber.open") as mock_open_pdf:
            mock_open_pdf.return_value.__enter__.return_value = mock_pdf
            result = DocumentParser._parse_pdf("/path/to/file.pdf")

            assert "Page 1 content" in result
            assert "Page 2 content" in result
            assert "Page 3 content" in result

    def test_parse_pdf_empty_page(self):
        """Test parsing PDF with empty pages."""
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"

        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = None  # Empty page

        mock_page3 = MagicMock()
        mock_page3.extract_text.return_value = "Page 3 content"

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page1, mock_page2, mock_page3]

        with patch("pdfplumber.open") as mock_open_pdf:
            mock_open_pdf.return_value.__enter__.return_value = mock_pdf
            result = DocumentParser._parse_pdf("/path/to/file.pdf")

            assert "Page 1 content" in result
            assert "Page 3 content" in result
            # Empty page should not add anything


class TestDocumentParserDOCX:
    """Tests for DOCX file parsing."""

    def test_parse_docx_basic(self):
        """Test parsing a basic DOCX file."""
        mock_para1 = MagicMock()
        mock_para1.text = "First paragraph"

        mock_para2 = MagicMock()
        mock_para2.text = "Second paragraph"

        mock_para3 = MagicMock()
        mock_para3.text = ""  # Empty paragraph

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2, mock_para3]

        with patch("app.services.parser.Document") as mock_docx:
            mock_docx.return_value = mock_doc
            result = DocumentParser._parse_docx("/path/to/file.docx")

            assert "First paragraph" in result
            assert "Second paragraph" in result
            # Empty paragraphs should be excluded
            assert result.count("First paragraph") == 1
            assert result.count("Second paragraph") == 1

    def test_parse_docx_all_empty(self):
        """Test parsing DOCX with all empty paragraphs."""
        mock_para = MagicMock()
        mock_para.text = ""

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para, mock_para]

        with patch("app.services.parser.Document") as mock_docx:
            mock_docx.return_value = mock_doc
            result = DocumentParser._parse_docx("/path/to/file.docx")

            assert result == ""


class TestDocumentParserIntegration:
    """Integration tests using the main parse method."""

    def test_parse_txt_through_main_method(self):
        """Test parsing TXT through the main parse method."""
        content = "Plain text content"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser.parse("/path/to/file.txt", "txt")
            assert result == content

    def test_parse_md_through_main_method(self):
        """Test parsing MD through the main parse method."""
        content = "# Header\n\nParagraph"
        with patch("builtins.open", mock_open(read_data=content)):
            result = DocumentParser.parse("/path/to/file.md", "md")
            assert "#" not in result
            assert "Header" in result

    def test_parse_pdf_through_main_method(self):
        """Test parsing PDF through the main parse method."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "PDF content"

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open") as mock_open_pdf:
            mock_open_pdf.return_value.__enter__.return_value = mock_pdf
            result = DocumentParser.parse("/path/to/file.pdf", "pdf")
            assert "PDF content" in result

    def test_parse_docx_through_main_method(self):
        """Test parsing DOCX through the main parse method."""
        mock_para = MagicMock()
        mock_para.text = "DOCX content"

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]

        with patch("app.services.parser.Document") as mock_docx:
            mock_docx.return_value = mock_doc
            result = DocumentParser.parse("/path/to/file.docx", "docx")
            assert "DOCX content" in result
