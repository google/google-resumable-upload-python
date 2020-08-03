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

import io
import json
import pytest

import mock
from six.moves import http_client
import aiohttp
#from aioresponses import aioresponses

import asyncio


from google import async_resumable_media
import google.async_resumable_media.requests.upload as upload_mod


SIMPLE_URL = (
    u"https://www.googleapis.com/upload/storage/v1/b/{BUCKET}/o?"
    u"uploadType=media&name={OBJECT}"
)
MULTIPART_URL = (
    u"https://www.googleapis.com/upload/storage/v1/b/{BUCKET}/o?"
    u"uploadType=multipart"
)
RESUMABLE_URL = (
    u"https://www.googleapis.com/upload/storage/v1/b/{BUCKET}/o?"
    u"uploadType=resumable"
)
ONE_MB = 1024 * 1024
BASIC_CONTENT = u"text/plain"
JSON_TYPE = u"application/json; charset=UTF-8"
JSON_TYPE_LINE = b"content-type: application/json; charset=UTF-8\r\n"
EXPECTED_TIMEOUT = 61

#test by making the upload async for that case

class TestSimpleUpload(object):
    
    @pytest.mark.asyncio
    async def test_transmit(self):
        data = b"I have got a lovely bunch of coconuts."
        content_type = BASIC_CONTENT
        upload = upload_mod.SimpleUpload(SIMPLE_URL)

        transport = mock.AsyncMock(spec=["request"])
        transport.request = mock.AsyncMock(spec = ["__call__"], return_value = _make_response())
        
        assert not upload.finished

        ret_val = await upload.transmit(transport, data, content_type)

        assert ret_val is transport.request.return_value

        upload_headers = {u"content-type": content_type}
        transport.request.assert_called_once_with(
            u"POST",
            SIMPLE_URL,
            data=data,
            headers=upload_headers,
            timeout=EXPECTED_TIMEOUT,
        )

        assert upload.finished


class TestMultipartUpload(object):

    @mock.patch(u"google.async_resumable_media._upload.get_boundary", return_value= b"==4==")
    @pytest.mark.asyncio
    async def test_transmit(self, mock_get_boundary):
        data = b"Mock data here and there."
        metadata = {u"Hey": u"You", u"Guys": u"90909"}
        content_type = BASIC_CONTENT
        upload = upload_mod.MultipartUpload(MULTIPART_URL)

        transport = mock.AsyncMock(spec=["request"])
        transport.request = mock.AsyncMock(spec = ["__call__"], return_value = _make_response())

        assert not upload.finished

        ret_val = await upload.transmit(transport, data, metadata, content_type)

        assert ret_val is transport.request.return_value

        expected_payload = (
            b"--==4==\r\n"
            + JSON_TYPE_LINE
            + b"\r\n"
            + json.dumps(metadata).encode(u"utf-8")
            + b"\r\n"
            + b"--==4==\r\n"
            b"content-type: text/plain\r\n"
            b"\r\n"
            b"Mock data here and there.\r\n"
            b"--==4==--"
        )
        multipart_type = b'multipart/related; boundary="==4=="'
        upload_headers = {u"content-type": multipart_type}
        transport.request.assert_called_once_with(
            u"POST",
            MULTIPART_URL,
            data=expected_payload,
            headers=upload_headers,
            timeout=EXPECTED_TIMEOUT,
        )
        assert upload.finished
        mock_get_boundary.assert_called_once_with()


class TestResumableUpload(object):

    @pytest.mark.asyncio
    async def test_initiate(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        data = b"Knock knock who is there"
        stream = io.BytesIO(data)
        metadata = {u"name": u"got-jokes.txt"}

        transport = mock.AsyncMock(spec=["request"])
        location = (u"http://test.invalid?upload_id=AACODBBBxuw9u3AA",)
        response_headers = {u"location": location}
        transport.request = mock.AsyncMock(spec = ["__call__"], return_value = _make_response(headers=response_headers))
        
        # Check resumable_url before.
        assert upload._resumable_url is None
        # Make request and check the return value (against the mock).
        total_bytes = 100
        assert total_bytes > len(data)
        response = await upload.initiate(
            transport,
            stream,
            metadata,
            BASIC_CONTENT,
            total_bytes=total_bytes,
            stream_final=False,
        )
        assert response is transport.request.return_value
        # Check resumable_url after.
        assert upload._resumable_url == location
        # Make sure the mock was called as expected.
        json_bytes = b'{"name": "got-jokes.txt"}'
        expected_headers = {
            u"content-type": JSON_TYPE,
            u"x-upload-content-type": BASIC_CONTENT,
            u"x-upload-content-length": u"{:d}".format(total_bytes),
        }
        transport.request.assert_called_once_with(
            u"POST",
            RESUMABLE_URL,
            data=json_bytes,
            headers=expected_headers,
            timeout=EXPECTED_TIMEOUT,
        )

    @staticmethod
    def _upload_in_flight(data, headers=None):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB, headers=headers)
        upload._stream = io.BytesIO(data)
        upload._content_type = BASIC_CONTENT
        upload._total_bytes = len(data)
        upload._resumable_url = u"http://test.invalid?upload_id=not-none"
        return upload

    @staticmethod
    def _chunk_mock(status_code, response_headers):
        transport = mock.AsyncMock(spec=["request"])
        put_response = _make_response(status_code=status_code, headers=response_headers)
        transport.request = mock.AsyncMock(spec = ["__call__"], return_value = put_response)

        return transport

    @pytest.mark.asyncio
    async def test_transmit_next_chunk(self):
        data = b"This time the data is official."
        upload = self._upload_in_flight(data)
        # Make a fake chunk size smaller than 256 KB.
        chunk_size = 10
        assert chunk_size < len(data)
        upload._chunk_size = chunk_size
        # Make a fake 308 response.
        response_headers = {u"range": u"bytes=0-{:d}".format(chunk_size - 1)}
        transport = self._chunk_mock(
            async_resumable_media.PERMANENT_REDIRECT, response_headers
        )
        # Check the state before the request.
        assert upload._bytes_uploaded == 0

        # Make request and check the return value (against the mock).
        response = await upload.transmit_next_chunk(transport)
        assert response is transport.request.return_value
        # Check that the state has been updated.
        assert upload._bytes_uploaded == chunk_size
        # Make sure the mock was called as expected.
        payload = data[:chunk_size]
        content_range = u"bytes 0-{:d}/{:d}".format(chunk_size - 1, len(data))
        expected_headers = {
            u"content-range": content_range,
            u"content-type": BASIC_CONTENT,
        }
        transport.request.assert_called_once_with(
            u"PUT",
            upload.resumable_url,
            data=payload,
            headers=expected_headers,
            timeout=EXPECTED_TIMEOUT,
        )

    @pytest.mark.asyncio
    async def test_recover(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True  # Make sure invalid.
        upload._stream = mock.Mock(spec=["seek"])
        upload._resumable_url = u"http://test.invalid?upload_id=big-deal"

        end = 55555
        headers = {u"range": u"bytes=0-{:d}".format(end)}
        transport = self._chunk_mock(async_resumable_media.PERMANENT_REDIRECT, headers)

        ret_val = await upload.recover(transport)
        assert ret_val is transport.request.return_value
        # Check the state of ``upload`` after.
        assert upload.bytes_uploaded == end + 1
        assert not upload.invalid
        upload._stream.seek.assert_called_once_with(end + 1)
        expected_headers = {u"content-range": u"bytes */*"}
        transport.request.assert_called_once_with(
            u"PUT",
            upload.resumable_url,
            data=None,
            headers=expected_headers,
            timeout=EXPECTED_TIMEOUT,
        )


def _make_response(status_code=200, headers=None):
    headers = headers or {}
    return mock.Mock(
        headers=headers, status=status_code, spec=["headers", "status_code"]
    )
