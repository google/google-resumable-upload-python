# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import hashlib
import io

import mock
import pytest
from six.moves import http_client

from google.resumable_media import common
from google.resumable_media import _helpers as _root_helpers
from google.resumable_media.requests import download as download_mod
from google.resumable_media.requests import _helpers


EXAMPLE_URL = (
    u"https://www.googleapis.com/download/storage/v1/b/"
    u"{BUCKET}/o/{OBJECT}?alt=media"
)
EXPECTED_TIMEOUT = (61, 60)


class TestDownload(object):
    def test__write_to_stream_no_hash_check(self):
        stream = io.BytesIO()
        download = download_mod.Download(EXAMPLE_URL, stream=stream)

        chunk1 = b"right now, "
        chunk2 = b"but a little later"
        response = _mock_response(chunks=[chunk1, chunk2], headers={})

        ret_val = download._write_to_stream(response)
        assert ret_val is None

        assert stream.getvalue() == chunk1 + chunk2

        # Check mocks.
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.iter_content.assert_called_once_with(
            chunk_size=_helpers._SINGLE_GET_CHUNK_SIZE, decode_unicode=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c", None])
    def test__write_to_stream_with_hash_check_success(self, checksum):
        stream = io.BytesIO()
        download = download_mod.Download(EXAMPLE_URL, stream=stream, checksum=checksum)

        chunk1 = b"first chunk, count starting at 0. "
        chunk2 = b"second chunk, or chunk 1, which is better? "
        chunk3 = b"ordinals and numerals and stuff."
        header_value = u"crc32c=qmNCyg==,md5=fPAJHnnoi/+NadyNxT2c2w=="
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_response(chunks=[chunk1, chunk2, chunk3], headers=headers)

        ret_val = download._write_to_stream(response)
        assert ret_val is None

        assert stream.getvalue() == chunk1 + chunk2 + chunk3

        # Check mocks.
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.iter_content.assert_called_once_with(
            chunk_size=_helpers._SINGLE_GET_CHUNK_SIZE, decode_unicode=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    def test__write_to_stream_with_hash_check_fail(self, checksum):
        stream = io.BytesIO()
        download = download_mod.Download(EXAMPLE_URL, stream=stream, checksum=checksum)

        chunk1 = b"first chunk, count starting at 0. "
        chunk2 = b"second chunk, or chunk 1, which is better? "
        chunk3 = b"ordinals and numerals and stuff."
        bad_checksum = u"d3JvbmcgbiBtYWRlIHVwIQ=="
        header_value = u"crc32c={bad},md5={bad}".format(bad=bad_checksum)
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_response(chunks=[chunk1, chunk2, chunk3], headers=headers)

        with pytest.raises(common.DataCorruption) as exc_info:
            download._write_to_stream(response)

        assert not download.finished

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 1
        if checksum == u"md5":
            good_checksum = u"fPAJHnnoi/+NadyNxT2c2w=="
        else:
            good_checksum = u"qmNCyg=="
        msg = download_mod._CHECKSUM_MISMATCH.format(
            EXAMPLE_URL, bad_checksum, good_checksum, checksum_type=checksum.upper()
        )
        assert error.args[0] == msg

        # Check mocks.
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.iter_content.assert_called_once_with(
            chunk_size=_helpers._SINGLE_GET_CHUNK_SIZE, decode_unicode=False
        )

    def test__write_to_stream_with_invalid_checksum_type(self):
        BAD_CHECKSUM_TYPE = "badsum"

        stream = io.BytesIO()
        download = download_mod.Download(
            EXAMPLE_URL, stream=stream, checksum=BAD_CHECKSUM_TYPE
        )

        chunk1 = b"first chunk, count starting at 0. "
        chunk2 = b"second chunk, or chunk 1, which is better? "
        chunk3 = b"ordinals and numerals and stuff."
        bad_checksum = u"d3JvbmcgbiBtYWRlIHVwIQ=="
        header_value = u"crc32c={bad},md5={bad}".format(bad=bad_checksum)
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_response(chunks=[chunk1, chunk2, chunk3], headers=headers)

        with pytest.raises(ValueError) as exc_info:
            download._write_to_stream(response)

        assert not download.finished

        error = exc_info.value
        assert error.args[0] == "checksum must be ``'md5'``, ``'crc32c'`` or ``None``"

    def _consume_helper(
        self,
        stream=None,
        end=65536,
        headers=None,
        chunks=(),
        response_headers=None,
        checksum="md5",
        timeout=None,
    ):
        download = download_mod.Download(
            EXAMPLE_URL, stream=stream, end=end, headers=headers, checksum=checksum
        )
        transport = mock.Mock(spec=["request"])
        transport.request.return_value = _mock_response(
            chunks=chunks, headers=response_headers
        )

        assert not download.finished

        if timeout is not None:
            ret_val = download.consume(transport, timeout=timeout)
        else:
            ret_val = download.consume(transport)

        assert ret_val is transport.request.return_value

        called_kwargs = {
            u"data": None,
            u"headers": download._headers,
            u"timeout": EXPECTED_TIMEOUT if timeout is None else timeout,
        }
        if chunks:
            assert stream is not None
            called_kwargs[u"stream"] = True

        transport.request.assert_called_once_with(u"GET", EXAMPLE_URL, **called_kwargs)

        range_bytes = u"bytes={:d}-{:d}".format(0, end)
        assert download._headers[u"range"] == range_bytes
        assert download.finished

        return transport

    def test_consume(self):
        self._consume_helper()

    def test_consume_with_custom_timeout(self):
        self._consume_helper(timeout=14.7)

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c", None])
    def test_consume_with_stream(self, checksum):
        stream = io.BytesIO()
        chunks = (b"up down ", b"charlie ", b"brown")
        transport = self._consume_helper(
            stream=stream, chunks=chunks, checksum=checksum
        )

        assert stream.getvalue() == b"".join(chunks)

        # Check mocks.
        response = transport.request.return_value
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.iter_content.assert_called_once_with(
            chunk_size=_helpers._SINGLE_GET_CHUNK_SIZE, decode_unicode=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    def test_consume_with_stream_hash_check_success(self, checksum):
        stream = io.BytesIO()
        chunks = (b"up down ", b"charlie ", b"brown")
        header_value = u"crc32c=UNIQxg==,md5=JvS1wjMvfbCXgEGeaJJLDQ=="
        headers = {download_mod._HASH_HEADER: header_value}
        transport = self._consume_helper(
            stream=stream, chunks=chunks, response_headers=headers, checksum=checksum
        )

        assert stream.getvalue() == b"".join(chunks)

        # Check mocks.
        response = transport.request.return_value
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.iter_content.assert_called_once_with(
            chunk_size=_helpers._SINGLE_GET_CHUNK_SIZE, decode_unicode=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    def test_consume_with_stream_hash_check_fail(self, checksum):
        stream = io.BytesIO()
        download = download_mod.Download(EXAMPLE_URL, stream=stream, checksum=checksum)

        chunks = (b"zero zero", b"niner tango")
        bad_checksum = u"anVzdCBub3QgdGhpcyAxLA=="
        header_value = u"crc32c={bad},md5={bad}".format(bad=bad_checksum)
        headers = {download_mod._HASH_HEADER: header_value}
        transport = mock.Mock(spec=["request"])
        transport.request.return_value = _mock_response(chunks=chunks, headers=headers)

        assert not download.finished
        with pytest.raises(common.DataCorruption) as exc_info:
            download.consume(transport)

        assert stream.getvalue() == b"".join(chunks)
        assert download.finished
        assert download._headers == {}

        error = exc_info.value
        assert error.response is transport.request.return_value
        assert len(error.args) == 1
        if checksum == u"md5":
            good_checksum = u"1A/dxEpys717C6FH7FIWDw=="
        else:
            good_checksum = u"GvNZlg=="
        msg = download_mod._CHECKSUM_MISMATCH.format(
            EXAMPLE_URL, bad_checksum, good_checksum, checksum_type=checksum.upper()
        )
        assert error.args[0] == msg

        # Check mocks.
        transport.request.assert_called_once_with(
            u"GET",
            EXAMPLE_URL,
            data=None,
            headers={},
            stream=True,
            timeout=EXPECTED_TIMEOUT,
        )

    def test_consume_with_headers(self):
        headers = {}  # Empty headers
        end = 16383
        self._consume_helper(end=end, headers=headers)
        range_bytes = u"bytes={:d}-{:d}".format(0, end)
        # Make sure the headers have been modified.
        assert headers == {u"range": range_bytes}


class TestRawDownload(object):
    def test__write_to_stream_no_hash_check(self):
        stream = io.BytesIO()
        download = download_mod.RawDownload(EXAMPLE_URL, stream=stream)

        chunk1 = b"right now, "
        chunk2 = b"but a little later"
        response = _mock_raw_response(chunks=[chunk1, chunk2], headers={})

        ret_val = download._write_to_stream(response)
        assert ret_val is None

        assert stream.getvalue() == chunk1 + chunk2

        # Check mocks.
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.raw.stream.assert_called_once_with(
            _helpers._SINGLE_GET_CHUNK_SIZE, decode_content=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c", None])
    def test__write_to_stream_with_hash_check_success(self, checksum):
        stream = io.BytesIO()
        download = download_mod.RawDownload(
            EXAMPLE_URL, stream=stream, checksum=checksum
        )

        chunk1 = b"first chunk, count starting at 0. "
        chunk2 = b"second chunk, or chunk 1, which is better? "
        chunk3 = b"ordinals and numerals and stuff."
        header_value = u"crc32c=qmNCyg==,md5=fPAJHnnoi/+NadyNxT2c2w=="
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_raw_response(chunks=[chunk1, chunk2, chunk3], headers=headers)

        ret_val = download._write_to_stream(response)
        assert ret_val is None

        assert stream.getvalue() == chunk1 + chunk2 + chunk3

        # Check mocks.
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.raw.stream.assert_called_once_with(
            _helpers._SINGLE_GET_CHUNK_SIZE, decode_content=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    def test__write_to_stream_with_hash_check_fail(self, checksum):
        stream = io.BytesIO()
        download = download_mod.RawDownload(
            EXAMPLE_URL, stream=stream, checksum=checksum
        )

        chunk1 = b"first chunk, count starting at 0. "
        chunk2 = b"second chunk, or chunk 1, which is better? "
        chunk3 = b"ordinals and numerals and stuff."
        bad_checksum = u"d3JvbmcgbiBtYWRlIHVwIQ=="
        header_value = u"crc32c={bad},md5={bad}".format(bad=bad_checksum)
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_raw_response(chunks=[chunk1, chunk2, chunk3], headers=headers)

        with pytest.raises(common.DataCorruption) as exc_info:
            download._write_to_stream(response)

        assert not download.finished

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 1
        if checksum == u"md5":
            good_checksum = u"fPAJHnnoi/+NadyNxT2c2w=="
        else:
            good_checksum = u"qmNCyg=="
        msg = download_mod._CHECKSUM_MISMATCH.format(
            EXAMPLE_URL, bad_checksum, good_checksum, checksum_type=checksum.upper()
        )
        assert error.args[0] == msg

        # Check mocks.
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.raw.stream.assert_called_once_with(
            _helpers._SINGLE_GET_CHUNK_SIZE, decode_content=False
        )

    def test__write_to_stream_with_invalid_checksum_type(self):
        BAD_CHECKSUM_TYPE = "badsum"

        stream = io.BytesIO()
        download = download_mod.RawDownload(
            EXAMPLE_URL, stream=stream, checksum=BAD_CHECKSUM_TYPE
        )

        chunk1 = b"first chunk, count starting at 0. "
        chunk2 = b"second chunk, or chunk 1, which is better? "
        chunk3 = b"ordinals and numerals and stuff."
        bad_checksum = u"d3JvbmcgbiBtYWRlIHVwIQ=="
        header_value = u"crc32c={bad},md5={bad}".format(bad=bad_checksum)
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_response(chunks=[chunk1, chunk2, chunk3], headers=headers)

        with pytest.raises(ValueError) as exc_info:
            download._write_to_stream(response)

        assert not download.finished

        error = exc_info.value
        assert error.args[0] == "checksum must be ``'md5'``, ``'crc32c'`` or ``None``"

    def _consume_helper(
        self,
        stream=None,
        end=65536,
        headers=None,
        chunks=(),
        response_headers=None,
        checksum=None,
        timeout=None,
    ):
        download = download_mod.RawDownload(
            EXAMPLE_URL, stream=stream, end=end, headers=headers, checksum=checksum
        )
        transport = mock.Mock(spec=["request"])
        transport.request.return_value = _mock_raw_response(
            chunks=chunks, headers=response_headers
        )

        assert not download.finished

        if timeout is not None:
            ret_val = download.consume(transport, timeout=timeout)
        else:
            ret_val = download.consume(transport)

        assert ret_val is transport.request.return_value

        if chunks:
            assert stream is not None
        transport.request.assert_called_once_with(
            u"GET",
            EXAMPLE_URL,
            data=None,
            headers=download._headers,
            stream=True,
            timeout=EXPECTED_TIMEOUT if timeout is None else timeout,
        )

        range_bytes = u"bytes={:d}-{:d}".format(0, end)
        assert download._headers[u"range"] == range_bytes
        assert download.finished

        return transport

    def test_consume(self):
        self._consume_helper()

    def test_consume_with_custom_timeout(self):
        self._consume_helper(timeout=14.7)

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c", None])
    def test_consume_with_stream(self, checksum):
        stream = io.BytesIO()
        chunks = (b"up down ", b"charlie ", b"brown")
        transport = self._consume_helper(
            stream=stream, chunks=chunks, checksum=checksum
        )

        assert stream.getvalue() == b"".join(chunks)

        # Check mocks.
        response = transport.request.return_value
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.raw.stream.assert_called_once_with(
            _helpers._SINGLE_GET_CHUNK_SIZE, decode_content=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    def test_consume_with_stream_hash_check_success(self, checksum):
        stream = io.BytesIO()
        chunks = (b"up down ", b"charlie ", b"brown")
        header_value = u"crc32c=UNIQxg==,md5=JvS1wjMvfbCXgEGeaJJLDQ=="
        headers = {download_mod._HASH_HEADER: header_value}
        transport = self._consume_helper(
            stream=stream, chunks=chunks, response_headers=headers, checksum=checksum
        )

        assert stream.getvalue() == b"".join(chunks)

        # Check mocks.
        response = transport.request.return_value
        response.__enter__.assert_called_once_with()
        response.__exit__.assert_called_once_with(None, None, None)
        response.raw.stream.assert_called_once_with(
            _helpers._SINGLE_GET_CHUNK_SIZE, decode_content=False
        )

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    def test_consume_with_stream_hash_check_fail(self, checksum):
        stream = io.BytesIO()
        download = download_mod.RawDownload(
            EXAMPLE_URL, stream=stream, checksum=checksum
        )

        chunks = (b"zero zero", b"niner tango")
        bad_checksum = u"anVzdCBub3QgdGhpcyAxLA=="
        header_value = u"crc32c={bad},md5={bad}".format(bad=bad_checksum)
        headers = {download_mod._HASH_HEADER: header_value}
        transport = mock.Mock(spec=["request"])
        transport.request.return_value = _mock_raw_response(
            chunks=chunks, headers=headers
        )

        assert not download.finished
        with pytest.raises(common.DataCorruption) as exc_info:
            download.consume(transport)

        assert stream.getvalue() == b"".join(chunks)
        assert download.finished
        assert download._headers == {}

        error = exc_info.value
        assert error.response is transport.request.return_value
        assert len(error.args) == 1
        if checksum == u"md5":
            good_checksum = u"1A/dxEpys717C6FH7FIWDw=="
        else:
            good_checksum = u"GvNZlg=="
        msg = download_mod._CHECKSUM_MISMATCH.format(
            EXAMPLE_URL, bad_checksum, good_checksum, checksum_type=checksum.upper()
        )
        assert error.args[0] == msg

        # Check mocks.
        transport.request.assert_called_once_with(
            u"GET",
            EXAMPLE_URL,
            data=None,
            headers={},
            stream=True,
            timeout=EXPECTED_TIMEOUT,
        )

    def test_consume_with_headers(self):
        headers = {}  # Empty headers
        end = 16383
        self._consume_helper(end=end, headers=headers)
        range_bytes = u"bytes={:d}-{:d}".format(0, end)
        # Make sure the headers have been modified.
        assert headers == {u"range": range_bytes}


class TestChunkedDownload(object):
    @staticmethod
    def _response_content_range(start_byte, end_byte, total_bytes):
        return u"bytes {:d}-{:d}/{:d}".format(start_byte, end_byte, total_bytes)

    def _response_headers(self, start_byte, end_byte, total_bytes):
        content_length = end_byte - start_byte + 1
        resp_range = self._response_content_range(start_byte, end_byte, total_bytes)
        return {
            u"content-length": u"{:d}".format(content_length),
            u"content-range": resp_range,
        }

    def _mock_response(
        self, start_byte, end_byte, total_bytes, content=None, status_code=None
    ):
        response_headers = self._response_headers(start_byte, end_byte, total_bytes)
        return mock.Mock(
            content=content,
            headers=response_headers,
            status_code=status_code,
            spec=["content", "headers", "status_code"],
        )

    def test_consume_next_chunk_already_finished(self):
        download = download_mod.ChunkedDownload(EXAMPLE_URL, 512, None)
        download._finished = True
        with pytest.raises(ValueError):
            download.consume_next_chunk(None)

    def _mock_transport(self, start, chunk_size, total_bytes, content=b""):
        transport = mock.Mock(spec=["request"])
        assert len(content) == chunk_size
        transport.request.return_value = self._mock_response(
            start,
            start + chunk_size - 1,
            total_bytes,
            content=content,
            status_code=int(http_client.OK),
        )

        return transport

    def test_consume_next_chunk(self):
        start = 1536
        stream = io.BytesIO()
        data = b"Just one chunk."
        chunk_size = len(data)
        download = download_mod.ChunkedDownload(
            EXAMPLE_URL, chunk_size, stream, start=start
        )
        total_bytes = 16384
        transport = self._mock_transport(start, chunk_size, total_bytes, content=data)

        # Verify the internal state before consuming a chunk.
        assert not download.finished
        assert download.bytes_downloaded == 0
        assert download.total_bytes is None
        # Actually consume the chunk and check the output.
        ret_val = download.consume_next_chunk(transport)
        assert ret_val is transport.request.return_value
        range_bytes = u"bytes={:d}-{:d}".format(start, start + chunk_size - 1)
        download_headers = {u"range": range_bytes}
        transport.request.assert_called_once_with(
            u"GET",
            EXAMPLE_URL,
            data=None,
            headers=download_headers,
            timeout=EXPECTED_TIMEOUT,
        )
        assert stream.getvalue() == data
        # Go back and check the internal state after consuming the chunk.
        assert not download.finished
        assert download.bytes_downloaded == chunk_size
        assert download.total_bytes == total_bytes

    def test_consume_next_chunk_with_custom_timeout(self):
        start = 1536
        stream = io.BytesIO()
        data = b"Just one chunk."
        chunk_size = len(data)
        download = download_mod.ChunkedDownload(
            EXAMPLE_URL, chunk_size, stream, start=start
        )
        total_bytes = 16384
        transport = self._mock_transport(start, chunk_size, total_bytes, content=data)

        # Actually consume the chunk and check the output.
        download.consume_next_chunk(transport, timeout=14.7)

        range_bytes = u"bytes={:d}-{:d}".format(start, start + chunk_size - 1)
        download_headers = {u"range": range_bytes}
        transport.request.assert_called_once_with(
            u"GET", EXAMPLE_URL, data=None, headers=download_headers, timeout=14.7,
        )


class TestRawChunkedDownload(object):
    @staticmethod
    def _response_content_range(start_byte, end_byte, total_bytes):
        return u"bytes {:d}-{:d}/{:d}".format(start_byte, end_byte, total_bytes)

    def _response_headers(self, start_byte, end_byte, total_bytes):
        content_length = end_byte - start_byte + 1
        resp_range = self._response_content_range(start_byte, end_byte, total_bytes)
        return {
            u"content-length": u"{:d}".format(content_length),
            u"content-range": resp_range,
        }

    def _mock_response(
        self, start_byte, end_byte, total_bytes, content=None, status_code=None
    ):
        response_headers = self._response_headers(start_byte, end_byte, total_bytes)
        return mock.Mock(
            _content=content,
            headers=response_headers,
            status_code=status_code,
            spec=["_content", "headers", "status_code"],
        )

    def test_consume_next_chunk_already_finished(self):
        download = download_mod.RawChunkedDownload(EXAMPLE_URL, 512, None)
        download._finished = True
        with pytest.raises(ValueError):
            download.consume_next_chunk(None)

    def _mock_transport(self, start, chunk_size, total_bytes, content=b""):
        transport = mock.Mock(spec=["request"])
        assert len(content) == chunk_size
        transport.request.return_value = self._mock_response(
            start,
            start + chunk_size - 1,
            total_bytes,
            content=content,
            status_code=int(http_client.OK),
        )

        return transport

    def test_consume_next_chunk(self):
        start = 1536
        stream = io.BytesIO()
        data = b"Just one chunk."
        chunk_size = len(data)
        download = download_mod.RawChunkedDownload(
            EXAMPLE_URL, chunk_size, stream, start=start
        )
        total_bytes = 16384
        transport = self._mock_transport(start, chunk_size, total_bytes, content=data)

        # Verify the internal state before consuming a chunk.
        assert not download.finished
        assert download.bytes_downloaded == 0
        assert download.total_bytes is None
        # Actually consume the chunk and check the output.
        ret_val = download.consume_next_chunk(transport)
        assert ret_val is transport.request.return_value
        range_bytes = u"bytes={:d}-{:d}".format(start, start + chunk_size - 1)
        download_headers = {u"range": range_bytes}
        transport.request.assert_called_once_with(
            u"GET",
            EXAMPLE_URL,
            data=None,
            headers=download_headers,
            stream=True,
            timeout=EXPECTED_TIMEOUT,
        )
        assert stream.getvalue() == data
        # Go back and check the internal state after consuming the chunk.
        assert not download.finished
        assert download.bytes_downloaded == chunk_size
        assert download.total_bytes == total_bytes

    def test_consume_next_chunk_with_custom_timeout(self):
        start = 1536
        stream = io.BytesIO()
        data = b"Just one chunk."
        chunk_size = len(data)
        download = download_mod.RawChunkedDownload(
            EXAMPLE_URL, chunk_size, stream, start=start
        )
        total_bytes = 16384
        transport = self._mock_transport(start, chunk_size, total_bytes, content=data)

        # Actually consume the chunk and check the output.
        download.consume_next_chunk(transport, timeout=14.7)

        range_bytes = u"bytes={:d}-{:d}".format(start, start + chunk_size - 1)
        download_headers = {u"range": range_bytes}
        transport.request.assert_called_once_with(
            u"GET",
            EXAMPLE_URL,
            data=None,
            headers=download_headers,
            stream=True,
            timeout=14.7,
        )
        assert stream.getvalue() == data
        # Go back and check the internal state after consuming the chunk.
        assert not download.finished
        assert download.bytes_downloaded == chunk_size
        assert download.total_bytes == total_bytes


class Test__get_expected_checksum(object):
    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    @mock.patch("google.resumable_media.requests.download._LOGGER")
    def test__w_header_present(self, _LOGGER, checksum):
        checksums = {"md5": u"b2twdXNodGhpc2J1dHRvbg==", "crc32c": u"3q2+7w=="}
        header_value = u"crc32c={},md5={}".format(checksums["crc32c"], checksums["md5"])
        headers = {download_mod._HASH_HEADER: header_value}
        response = _mock_response(headers=headers)

        def _get_headers(response):
            return response.headers

        expected_checksum, checksum_obj = download_mod._get_expected_checksum(
            response, _get_headers, EXAMPLE_URL, checksum_type=checksum
        )
        assert expected_checksum == checksums[checksum]

        checksum_types = {
            "md5": type(hashlib.md5()),
            "crc32c": type(_root_helpers._get_crc32c_object()),
        }
        assert isinstance(checksum_obj, checksum_types[checksum])

        _LOGGER.info.assert_not_called()

    @pytest.mark.parametrize("checksum", [u"md5", u"crc32c"])
    @mock.patch("google.resumable_media.requests.download._LOGGER")
    def test__w_header_missing(self, _LOGGER, checksum):
        headers = {}
        response = _mock_response(headers=headers)

        def _get_headers(response):
            return response.headers

        expected_checksum, checksum_obj = download_mod._get_expected_checksum(
            response, _get_headers, EXAMPLE_URL, checksum_type=checksum
        )
        assert expected_checksum is None
        assert isinstance(checksum_obj, download_mod._DoNothingHash)
        expected_msg = download_mod._MISSING_CHECKSUM.format(
            EXAMPLE_URL, checksum_type=checksum.upper()
        )
        _LOGGER.info.assert_called_once_with(expected_msg)


class Test__parse_checksum_header(object):

    CRC32C_CHECKSUM = u"3q2+7w=="
    MD5_CHECKSUM = u"c2l4dGVlbmJ5dGVzbG9uZw=="

    def test_empty_value(self):
        header_value = None
        response = None
        md5_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="md5"
        )
        assert md5_header is None
        crc32c_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="crc32c"
        )
        assert crc32c_header is None

    def test_crc32c_only(self):
        header_value = u"crc32c={}".format(self.CRC32C_CHECKSUM)
        response = None
        md5_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="md5"
        )
        assert md5_header is None
        crc32c_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="crc32c"
        )
        assert crc32c_header == self.CRC32C_CHECKSUM

    def test_md5_only(self):
        header_value = u"md5={}".format(self.MD5_CHECKSUM)
        response = None
        md5_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="md5"
        )
        assert md5_header == self.MD5_CHECKSUM
        crc32c_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="crc32c"
        )
        assert crc32c_header is None

    def test_both_crc32c_and_md5(self):
        header_value = u"crc32c={},md5={}".format(
            self.CRC32C_CHECKSUM, self.MD5_CHECKSUM
        )
        response = None
        md5_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="md5"
        )
        assert md5_header == self.MD5_CHECKSUM
        crc32c_header = download_mod._parse_checksum_header(
            header_value, response, checksum_label="crc32c"
        )
        assert crc32c_header == self.CRC32C_CHECKSUM

    def test_md5_multiple_matches(self):
        another_checksum = u"eW91IGRpZCBXQVQgbm93Pw=="
        header_value = u"md5={},md5={}".format(self.MD5_CHECKSUM, another_checksum)
        response = mock.sentinel.response

        with pytest.raises(common.InvalidResponse) as exc_info:
            download_mod._parse_checksum_header(
                header_value, response, checksum_label="md5"
            )

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 3
        assert error.args[1] == header_value
        assert error.args[2] == [self.MD5_CHECKSUM, another_checksum]


def test__DoNothingHash():
    do_nothing_hash = download_mod._DoNothingHash()
    return_value = do_nothing_hash.update(b"some data")
    assert return_value is None


class Test__add_decoder(object):
    def test_non_gzipped(self):
        response_raw = mock.Mock(headers={}, spec=["headers"])
        md5_hash = download_mod._add_decoder(response_raw, mock.sentinel.md5_hash)

        assert md5_hash is mock.sentinel.md5_hash

    def test_gzipped(self):
        headers = {u"content-encoding": u"gzip"}
        response_raw = mock.Mock(headers=headers, spec=["headers", "_decoder"])
        md5_hash = download_mod._add_decoder(response_raw, mock.sentinel.md5_hash)

        assert md5_hash is not mock.sentinel.md5_hash
        assert isinstance(md5_hash, download_mod._DoNothingHash)
        assert isinstance(response_raw._decoder, download_mod._GzipDecoder)
        assert response_raw._decoder._checksum is mock.sentinel.md5_hash


class Test_GzipDecoder(object):
    def test_constructor(self):
        decoder = download_mod._GzipDecoder(mock.sentinel.md5_hash)
        assert decoder._checksum is mock.sentinel.md5_hash

    def test_decompress(self):
        md5_hash = mock.Mock(spec=["update"])
        decoder = download_mod._GzipDecoder(md5_hash)

        data = b"\x1f\x8b\x08\x08"
        result = decoder.decompress(data)

        assert result == b""
        md5_hash.update.assert_called_once_with(data)


def _mock_response(status_code=http_client.OK, chunks=(), headers=None):
    if headers is None:
        headers = {}

    if chunks:
        mock_raw = mock.Mock(headers=headers, spec=["headers"])
        response = mock.MagicMock(
            headers=headers,
            status_code=int(status_code),
            raw=mock_raw,
            spec=[
                u"__enter__",
                u"__exit__",
                u"iter_content",
                u"status_code",
                u"headers",
                u"raw",
            ],
        )
        # i.e. context manager returns ``self``.
        response.__enter__.return_value = response
        response.__exit__.return_value = None
        response.iter_content.return_value = iter(chunks)
        return response
    else:
        return mock.Mock(
            headers=headers,
            status_code=int(status_code),
            spec=["status_code", "headers"],
        )


def _mock_raw_response(status_code=http_client.OK, chunks=(), headers=None):
    if headers is None:
        headers = {}

    mock_raw = mock.Mock(headers=headers, spec=["stream"])
    mock_raw.stream.return_value = iter(chunks)
    response = mock.MagicMock(
        headers=headers,
        status_code=int(status_code),
        raw=mock_raw,
        spec=[
            u"__enter__",
            u"__exit__",
            u"iter_content",
            u"status_code",
            u"headers",
            u"raw",
        ],
    )
    # i.e. context manager returns ``self``.
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    return response
