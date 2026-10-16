"""Tests for the image_analyze base64 streaming encoder."""

import base64

import pytest

from codeassist.tools.image_analyze import _encode_image_base64


class TestImageBase64Encoding:
    """Streaming the base64 encode must produce exactly what encoding the whole
    file at once would, for any size (including non-multiples of 3)."""

    @pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 5, 7, 100, 1023, 1024, 4096])
    def test_single_chunk_matches_plain_encoding(self, tmp_path, size):
        data = bytes((i * 7) % 256 for i in range(size))
        f = tmp_path / "img.bin"
        f.write_bytes(data)
        assert _encode_image_base64(f) == base64.b64encode(data).decode("ascii")

    @pytest.mark.parametrize("size", [6, 9, 15, 100, 1001])
    def test_multiple_small_chunks_match_plain_encoding(self, tmp_path, size):
        data = bytes((i * 13) % 256 for i in range(size))
        f = tmp_path / "img.bin"
        f.write_bytes(data)
        # chunk_size=3 forces one base64 group per chunk; concatenating the
        # per-chunk encodings must equal encoding the whole file at once (any
        # padding only ever lands on the final chunk).
        assert _encode_image_base64(f, chunk_size=3) == base64.b64encode(data).decode("ascii")

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert _encode_image_base64(f) == ""
