"""#47 ``GET /api/covers/{filename}`` 静态封面端点的契约测试。"""
import pytest
from fastapi.testclient import TestClient

from app.api import covers as covers_module
from app.core.config import Settings
from app.main import app

client = TestClient(app)


@pytest.fixture
def cover_dir(tmp_path, monkeypatch):
    """把 cover_dir 指向临时目录，隔离真实上传目录。"""
    cover_path = tmp_path / "covers"
    cover_path.mkdir()
    fake_settings = Settings(cover_dir=str(cover_path))
    monkeypatch.setattr(covers_module, "get_settings", lambda: fake_settings)
    return cover_path


class TestCoverEndpoint:
    """静态封面端点：mime 白名单、路径穿越防护、404 语义。"""

    def test_returns_jpg_with_correct_mime(self, cover_dir):
        (cover_dir / "book.jpg").write_bytes(b"fake-jpeg-bytes")

        response = client.get("/api/covers/book.jpg")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == b"fake-jpeg-bytes"

    def test_returns_png_with_correct_mime(self, cover_dir):
        (cover_dir / "book.png").write_bytes(b"fake-png-bytes")

        response = client.get("/api/covers/book.png")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_returns_webp_with_correct_mime(self, cover_dir):
        (cover_dir / "book.webp").write_bytes(b"fake-webp-bytes")

        response = client.get("/api/covers/book.webp")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"

    def test_uppercase_extension_is_normalized(self, cover_dir):
        (cover_dir / "book.PNG").write_bytes(b"fake-png-bytes")

        response = client.get("/api/covers/book.PNG")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_rejects_non_whitelist_extension(self, cover_dir):
        (cover_dir / "book.exe").write_bytes(b"fake-exe-bytes")

        response = client.get("/api/covers/book.exe")

        assert response.status_code == 404

    def test_rejects_extensionless_filename(self, cover_dir):
        (cover_dir / "book").write_bytes(b"x")

        response = client.get("/api/covers/book")

        assert response.status_code == 404

    def test_missing_file_returns_404(self, cover_dir):
        response = client.get("/api/covers/ghost.png")

        assert response.status_code == 404

    def test_encoded_traversal_is_rejected(self, cover_dir):
        """``%2e%2e/`` 解码后为 ``../``，不得逃出 cover_dir。"""
        (cover_dir.parent / "outside.png").write_bytes(b"secret")

        response = client.get("/api/covers/%2e%2e/outside.png")

        assert response.status_code == 404


class TestResolveCoverPath:
    """路径解析防护的单元级验证。"""

    def test_rejects_dotdot_traversal(self, cover_dir):
        (cover_dir.parent / "outside.png").write_bytes(b"secret")

        assert covers_module._resolve_cover_path("../outside.png") is None

    def test_rejects_deep_traversal(self, cover_dir):
        (cover_dir.parent / "outside.png").write_bytes(b"secret")

        assert covers_module._resolve_cover_path("../../outside.png") is None

    def test_rejects_absolute_path(self, cover_dir):
        assert covers_module._resolve_cover_path("/etc/passwd") is None

    def test_accepts_plain_filename(self, cover_dir):
        (cover_dir / "book.png").write_bytes(b"x")

        result = covers_module._resolve_cover_path("book.png")

        assert result is not None
        assert result == (cover_dir / "book.png").resolve()

    def test_rejects_directory_as_target(self, cover_dir):
        (cover_dir / "subdir.png").mkdir()

        assert covers_module._resolve_cover_path("subdir.png") is None
