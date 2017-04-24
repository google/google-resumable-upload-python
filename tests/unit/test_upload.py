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
import sys

import mock
import pytest
from six.moves import http_client

from google.resumable_media import exceptions
import google.resumable_media.upload as upload_mod


SIMPLE_URL = (
    u'https://www.googleapis.com/upload/storage/v1/b/{BUCKET}/o?'
    u'uploadType=media&name={OBJECT}')
MULTIPART_URL = (
    u'https://www.googleapis.com/upload/storage/v1/b/{BUCKET}/o?'
    u'uploadType=multipart')
RESUMABLE_URL = (
    u'https://www.googleapis.com/upload/storage/v1/b/{BUCKET}/o?'
    u'uploadType=resumable')
ONE_MB = 1024 * 1024
BASIC_CONTENT = u'text/plain'
JSON_TYPE = u'application/json; charset=UTF-8'
JSON_TYPE_LINE = b'content-type: application/json; charset=UTF-8\r\n'


class Test_UploadBase(object):

    def test_constructor_defaults(self):
        upload = upload_mod._UploadBase(SIMPLE_URL)
        assert upload.upload_url == SIMPLE_URL
        assert upload._headers == {}
        assert not upload._finished

    def test_constructor_explicit(self):
        headers = {u'spin': u'doctors'}
        upload = upload_mod._UploadBase(SIMPLE_URL, headers=headers)
        assert upload.upload_url == SIMPLE_URL
        assert upload._headers is headers
        assert not upload._finished

    def test_finished_property(self):
        upload = upload_mod._UploadBase(SIMPLE_URL)
        # Default value of @property.
        assert not upload.finished

        # Make sure we cannot set it on public @property.
        with pytest.raises(AttributeError):
            upload.finished = False

        # Set it privately and then check the @property.
        upload._finished = True
        assert upload.finished

    def test__process_response_bad_status(self):
        upload = upload_mod._UploadBase(SIMPLE_URL)
        # Make sure **not finished** before.
        assert not upload.finished
        status_code = http_client.SERVICE_UNAVAILABLE
        response = _make_response(status_code=status_code)
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_response(response)

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 4
        assert error.args[1] == status_code
        assert error.args[3] == http_client.OK
        # Make sure **finished** after (even in failure).
        assert upload.finished

    def test__process_response(self):
        upload = upload_mod._UploadBase(SIMPLE_URL)
        # Make sure **not finished** before.
        assert not upload.finished
        response = _make_response()
        ret_val = upload._process_response(response)
        assert ret_val is None
        # Make sure **finished** after.
        assert upload.finished


class TestSimpleUpload(object):

    def test__prepare_request_already_finished(self):
        upload = upload_mod.SimpleUpload(SIMPLE_URL)
        upload._finished = True
        with pytest.raises(ValueError):
            upload._prepare_request(None)

    def test__prepare_request(self):
        upload = upload_mod.SimpleUpload(SIMPLE_URL)
        content_type = u'image/jpeg'
        headers = upload._prepare_request(content_type)
        assert headers == {u'content-type': content_type}

    def test__prepare_request_with_headers(self):
        headers = {u'x-goog-cheetos': u'spicy'}
        upload = upload_mod.SimpleUpload(SIMPLE_URL, headers=headers)
        content_type = u'image/jpeg'
        new_headers = upload._prepare_request(content_type)
        assert new_headers is headers
        expected = {u'content-type': content_type, u'x-goog-cheetos': u'spicy'}
        assert headers == expected

    def test_transmit(self):
        data = b'I have got a lovely bunch of coconuts.'
        content_type = BASIC_CONTENT
        upload = upload_mod.SimpleUpload(SIMPLE_URL)

        transport = mock.Mock(spec=[u'request'])
        transport.request.return_value = _make_response()
        assert not upload.finished
        ret_val = upload.transmit(transport, data, content_type)
        assert ret_val is transport.request.return_value
        upload_headers = {u'content-type': content_type}
        transport.request.assert_called_once_with(
            u'POST', SIMPLE_URL, data=data, headers=upload_headers)
        assert upload.finished


class TestMultipartUpload(object):

    def test__prepare_request_already_finished(self):
        upload = upload_mod.MultipartUpload(MULTIPART_URL)
        upload._finished = True
        with pytest.raises(ValueError):
            upload._prepare_request(b'Hi', {}, BASIC_CONTENT)

    def test__prepare_request_non_bytes_data(self):
        data = u'Nope not bytes.'
        upload = upload_mod.MultipartUpload(MULTIPART_URL)
        with pytest.raises(TypeError):
            upload._prepare_request(data, {}, BASIC_CONTENT)

    @mock.patch(u'google.resumable_media.upload._get_boundary',
                return_value=b'==3==')
    def _prepare_request_helper(self, mock_get_boundary, headers=None):
        upload = upload_mod.MultipartUpload(MULTIPART_URL, headers=headers)
        data = b'Hi'
        metadata = {u'Some': u'Stuff'}
        content_type = BASIC_CONTENT
        payload, new_headers = upload._prepare_request(
            data, metadata, content_type)

        expected_payload = (
            b'--==3==\r\n' +
            JSON_TYPE_LINE +
            b'\r\n'
            b'{"Some": "Stuff"}\r\n'
            b'--==3==\r\n'
            b'content-type: text/plain\r\n'
            b'\r\n'
            b'Hi\r\n'
            b'--==3==--')
        assert payload == expected_payload
        multipart_type = b'multipart/related; boundary="==3=="'
        mock_get_boundary.assert_called_once_with()

        return new_headers, multipart_type

    def test__prepare_request(self):
        headers, multipart_type = self._prepare_request_helper()
        assert headers == {u'content-type': multipart_type}

    def test__prepare_request_with_headers(self):
        headers = {u'best': u'shirt', u'worst': u'hat'}
        new_headers, multipart_type = self._prepare_request_helper(
            headers=headers)
        assert new_headers is headers
        expected_headers = {
            u'best': u'shirt',
            u'content-type': multipart_type,
            u'worst': u'hat',
        }
        assert expected_headers == headers

    @mock.patch(u'google.resumable_media.upload._get_boundary',
                return_value=b'==4==')
    def test_transmit(self, mock_get_boundary):
        data = b'Mock data here and there.'
        metadata = {u'Hey': u'You', u'Guys': u'90909'}
        content_type = BASIC_CONTENT
        upload = upload_mod.MultipartUpload(MULTIPART_URL)

        transport = mock.Mock(spec=[u'request'])
        transport.request.return_value = _make_response()
        assert not upload.finished
        ret_val = upload.transmit(transport, data, metadata, content_type)
        assert ret_val is transport.request.return_value
        expected_payload = (
            b'--==4==\r\n' +
            JSON_TYPE_LINE +
            b'\r\n' +
            json.dumps(metadata).encode(u'utf-8') + b'\r\n' +
            b'--==4==\r\n'
            b'content-type: text/plain\r\n'
            b'\r\n'
            b'Mock data here and there.\r\n'
            b'--==4==--')
        multipart_type = b'multipart/related; boundary="==4=="'
        upload_headers = {u'content-type': multipart_type}
        transport.request.assert_called_once_with(
            u'POST', MULTIPART_URL, data=expected_payload,
            headers=upload_headers)
        assert upload.finished
        mock_get_boundary.assert_called_once_with()


class TestResumableUpload(object):

    def test_constructor(self):
        chunk_size = ONE_MB
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, chunk_size)
        assert upload.upload_url == RESUMABLE_URL
        assert upload._headers == {}
        assert not upload._finished
        assert upload._chunk_size == chunk_size
        assert upload._stream is None
        assert upload._content_type is None
        assert upload._bytes_uploaded == 0
        assert upload._total_bytes is None
        assert upload._resumable_url is None

    def test_constructor_bad_chunk_size(self):
        with pytest.raises(ValueError):
            upload_mod.ResumableUpload(RESUMABLE_URL, 1)

    def test_invalid_property(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Default value of @property.
        assert not upload.invalid

        # Make sure we cannot set it on public @property.
        with pytest.raises(AttributeError):
            upload.invalid = False

        # Set it privately and then check the @property.
        upload._invalid = True
        assert upload.invalid

    def test_chunk_size_property(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Default value of @property.
        assert upload.chunk_size == ONE_MB

        # Make sure we cannot set it on public @property.
        with pytest.raises(AttributeError):
            upload.chunk_size = 17

        # Set it privately and then check the @property.
        new_size = 102
        upload._chunk_size = new_size
        assert upload.chunk_size == new_size

    def test_resumable_url_property(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Default value of @property.
        assert upload.resumable_url is None

        # Make sure we cannot set it on public @property.
        new_url = u'http://test.invalid?upload_id=not-none'
        with pytest.raises(AttributeError):
            upload.resumable_url = new_url

        # Set it privately and then check the @property.
        upload._resumable_url = new_url
        assert upload.resumable_url == new_url

    def test_bytes_uploaded_property(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Default value of @property.
        assert upload.bytes_uploaded == 0

        # Make sure we cannot set it on public @property.
        with pytest.raises(AttributeError):
            upload.bytes_uploaded = 1024

        # Set it privately and then check the @property.
        upload._bytes_uploaded = 128
        assert upload.bytes_uploaded == 128

    def test_total_bytes_property(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Default value of @property.
        assert upload.total_bytes is None

        # Make sure we cannot set it on public @property.
        with pytest.raises(AttributeError):
            upload.total_bytes = 65536

        # Set it privately and then check the @property.
        upload._total_bytes = 8192
        assert upload.total_bytes == 8192

    def _prepare_initiate_request_helper(self, upload_headers=None):
        data = b'some really big big data.'
        stream = io.BytesIO(data)
        metadata = {u'name': u'big-data-file.txt'}

        upload = upload_mod.ResumableUpload(
            RESUMABLE_URL, ONE_MB, headers=upload_headers)
        orig_headers = upload._headers.copy()
        # Check ``upload``-s state before.
        assert upload._stream is None
        assert upload._content_type is None
        assert upload._total_bytes is None
        # Call the method and check the output.
        payload, headers = upload._prepare_initiate_request(
            stream, metadata, BASIC_CONTENT)
        assert payload == b'{"name": "big-data-file.txt"}'
        # Make sure the ``upload``-s state was updated.
        assert upload._stream == stream
        assert upload._content_type == BASIC_CONTENT
        assert upload._total_bytes == len(data)
        # Make sure headers are untouched.
        assert headers is not upload._headers
        assert upload._headers == orig_headers
        # Make sure the stream is still at the beginning.
        assert stream.tell() == 0

        return data, headers

    def test__prepare_initiate_request(self):
        data, headers = self._prepare_initiate_request_helper()
        expected_headers = {
            u'content-type': JSON_TYPE,
            u'x-upload-content-length': u'{:d}'.format(len(data)),
            u'x-upload-content-type': BASIC_CONTENT,
        }
        assert headers == expected_headers

    def test__prepare_initiate_request_with_headers(self):
        headers = {u'caviar': u'beluga', u'top': u'quark'}
        data, new_headers = self._prepare_initiate_request_helper(
            upload_headers=headers)
        expected_headers = {
            u'caviar': u'beluga',
            u'content-type': JSON_TYPE,
            u'top': u'quark',
            u'x-upload-content-length': u'{:d}'.format(len(data)),
            u'x-upload-content-type': BASIC_CONTENT,
        }
        assert new_headers == expected_headers

    def test__prepare_initiate_request_already_initiated(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Fake that the upload has been started.
        upload._resumable_url = (
            u'http://test.invalid?upload_id=definitely-started')

        with pytest.raises(ValueError):
            upload._prepare_initiate_request(io.BytesIO(), {}, BASIC_CONTENT)

    def test__prepare_initiate_request_bad_stream_position(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)

        stream = io.BytesIO(b'data')
        stream.seek(1)
        with pytest.raises(ValueError):
            upload._prepare_initiate_request(stream, {}, BASIC_CONTENT)

        # Also test a bad object (i.e. non-stream)
        with pytest.raises(AttributeError):
            upload._prepare_initiate_request(None, {}, BASIC_CONTENT)

    def test__process_initiate_response_bad_response(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        response = mock.Mock(headers={}, spec=[u'headers'])
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_initiate_response(response)

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 2
        assert error.args[1] == u'location'

    def test__process_initiate_response(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)

        headers = {u'location': u'http://test.invalid?upload_id=kmfeij3234'}
        response = mock.Mock(headers=headers, spec=[u'headers'])
        # Check resumable_url before.
        assert upload._resumable_url is None
        # Process the actual headers.
        ret_val = upload._process_initiate_response(response)
        assert ret_val is None
        # Check resumable_url after.
        assert upload._resumable_url == headers[u'location']

    def test_initiate(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        data = b'Knock knock who is there'
        stream = io.BytesIO(data)
        metadata = {u'name': u'got-jokes.txt'}

        transport = mock.Mock(spec=[u'request'])
        location = u'http://test.invalid?upload_id=AACODBBBxuw9u3AA',
        response_headers = {u'location': location}
        post_response = _make_response(headers=response_headers)
        transport.request.return_value = post_response
        # Check resumable_url before.
        assert upload._resumable_url is None
        # Make request and check the return value (against the mock).
        response = upload.initiate(transport, stream, metadata, BASIC_CONTENT)
        assert response is transport.request.return_value
        # Check resumable_url after.
        assert upload._resumable_url == location
        # Make sure the mock was called as expected.
        json_bytes = b'{"name": "got-jokes.txt"}'
        expected_headers = {
            u'content-type': JSON_TYPE,
            u'x-upload-content-type': BASIC_CONTENT,
            u'x-upload-content-length': u'{:d}'.format(len(data)),
        }
        transport.request.assert_called_once_with(
            u'POST', RESUMABLE_URL, data=json_bytes, headers=expected_headers)

    def test__prepare_request_already_finished(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._finished = True
        with pytest.raises(ValueError) as exc_info:
            upload._prepare_request()

        assert exc_info.value.args == (u'Upload has finished.',)

    def test__prepare_request_invalid(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        assert not upload.finished
        upload._invalid = True
        with pytest.raises(ValueError) as exc_info:
            upload._prepare_request()

        assert exc_info.match(u'invalid state')
        assert exc_info.match(u'recover()')

    def test__prepare_request_not_initiated(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        assert not upload._finished
        assert upload._resumable_url is None
        with pytest.raises(ValueError) as exc_info:
            upload._prepare_request()

        assert exc_info.match(u'upload has not been initiated')
        assert exc_info.match(u'initiate()')

    def test__prepare_request_invalid_stream_state(self):
        stream = io.BytesIO(b'some data here')
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._stream = stream
        upload._resumable_url = u'http://test.invalid?upload_id=not-none'
        # Make stream.tell() disagree with bytes_uploaded.
        upload._bytes_uploaded = 5
        assert upload.bytes_uploaded != stream.tell()
        with pytest.raises(ValueError) as exc_info:
            upload._prepare_request()

        assert exc_info.match(u'Bytes stream is in unexpected state.')

    @staticmethod
    def _upload_in_flight(data, headers=None):
        upload = upload_mod.ResumableUpload(
            RESUMABLE_URL, ONE_MB, headers=headers)
        upload._stream = io.BytesIO(data)
        upload._content_type = BASIC_CONTENT
        upload._total_bytes = len(data)
        upload._resumable_url = u'http://test.invalid?upload_id=not-none'
        return upload

    def _prepare_request_helper(self, headers=None):
        data = b'All of the data goes in a stream.'
        upload = self._upload_in_flight(data, headers=headers)
        payload, new_headers = upload._prepare_request()
        # Check the response values.
        assert payload == data
        # Make sure headers are **NOT** updated
        assert upload._headers != new_headers

        return new_headers

    def test__prepare_request_success(self):
        headers = self._prepare_request_helper()
        expected_headers = {
            u'content-range': u'bytes 0-32/33',
            u'content-type': BASIC_CONTENT,
        }
        assert headers == expected_headers

    def test__prepare_request_success_with_headers(self):
        headers = {u'cannot': u'touch this'}
        new_headers = self._prepare_request_helper(headers)
        assert new_headers is not headers
        expected_headers = {
            u'content-range': u'bytes 0-32/33',
            u'content-type': BASIC_CONTENT,
        }
        assert new_headers == expected_headers
        # Make sure the ``_headers`` are not incorporated.
        assert u'cannot' not in new_headers

    def test__make_invalid(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        assert not upload.invalid
        upload._make_invalid()
        assert upload.invalid

    def test__process_response_bad_status(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Make sure the upload is valid before the failure.
        assert not upload.invalid
        response = _make_response(status_code=http_client.NOT_FOUND)
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_response(response)

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 5
        assert error.args[1] == response.status_code
        assert error.args[3] == http_client.OK
        assert error.args[4] == upload_mod.PERMANENT_REDIRECT
        # Make sure the upload is invalid after the failure.
        assert upload.invalid

    def test__process_response_success(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._total_bytes = mock.sentinel.total_bytes
        # Check status before.
        assert upload._bytes_uploaded == 0
        assert not upload._finished
        response = _make_response()
        ret_val = upload._process_response(response)
        assert ret_val is None
        # Check status after.
        assert upload._bytes_uploaded is mock.sentinel.total_bytes
        assert upload._finished

    def test__process_response_partial_no_range(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        response = _make_response(status_code=upload_mod.PERMANENT_REDIRECT)
        # Make sure the upload is valid before the failure.
        assert not upload.invalid
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_response(response)
        # Make sure the upload is invalid after the failure.
        assert upload.invalid

        # Check the error response.
        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 2
        assert error.args[1] == u'range'

    def test__process_response_partial_bad_range(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Make sure the upload is valid before the failure.
        assert not upload.invalid
        headers = {u'range': u'nights 1-81'}
        response = _make_response(
            status_code=upload_mod.PERMANENT_REDIRECT, headers=headers)
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_response(response)

        # Check the error response.
        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 3
        assert error.args[1] == headers[u'range']
        # Make sure the upload is invalid after the failure.
        assert upload.invalid

    def test__process_response_partial(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        # Check status before.
        assert upload._bytes_uploaded == 0
        headers = {u'range': u'bytes=0-171'}
        response = _make_response(
            status_code=upload_mod.PERMANENT_REDIRECT, headers=headers)
        ret_val = upload._process_response(response)
        assert ret_val is None
        # Check status after.
        assert upload._bytes_uploaded == 172

    @staticmethod
    def _chunk_mock(status_code, response_headers):
        transport = mock.Mock(spec=[u'request'])
        put_response = _make_response(
            status_code=status_code, headers=response_headers)
        transport.request.return_value = put_response

        return transport

    def test_transmit_next_chunk(self):
        data = b'This time the data is official.'
        upload = self._upload_in_flight(data)
        # Make a fake chunk size smaller than 256 KB.
        chunk_size = 10
        assert chunk_size < len(data)
        upload._chunk_size = chunk_size
        # Make a fake 308 response.
        response_headers = {u'range': u'bytes=0-{:d}'.format(chunk_size - 1)}
        transport = self._chunk_mock(
            upload_mod.PERMANENT_REDIRECT, response_headers)
        # Check the state before the request.
        assert upload._bytes_uploaded == 0

        # Make request and check the return value (against the mock).
        response = upload.transmit_next_chunk(transport)
        assert response is transport.request.return_value
        # Check that the state has been updated.
        assert upload._bytes_uploaded == chunk_size
        # Make sure the mock was called as expected.
        payload = data[:chunk_size]
        content_range = u'bytes 0-{:d}/{:d}'.format(chunk_size - 1, len(data))
        expected_headers = {
            u'content-range': content_range,
            u'content-type': BASIC_CONTENT,
        }
        transport.request.assert_called_once_with(
            u'PUT', upload.resumable_url, data=payload,
            headers=expected_headers)

    def test__prepare_recover_request_not_invalid(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        assert not upload.invalid

        with pytest.raises(ValueError):
            upload._prepare_recover_request()

    def test__prepare_recover_request(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True

        headers = upload._prepare_recover_request()
        assert headers == {u'content-range': u'bytes */*'}
        # Make sure headers are untouched.
        assert upload._headers == {}

    def test__prepare_recover_request_with_headers(self):
        headers = {u'lake': u'ocean'}
        upload = upload_mod.ResumableUpload(
            RESUMABLE_URL, ONE_MB, headers=headers)
        upload._invalid = True

        new_headers = upload._prepare_recover_request()
        assert new_headers == {u'content-range': u'bytes */*'}
        # Make sure the ``_headers`` are not incorporated.
        assert u'lake' not in new_headers
        # Make sure headers are untouched.
        assert upload._headers == {u'lake': u'ocean'}

    def test__process_recover_response_bad_status(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True

        response = _make_response(status_code=http_client.BAD_REQUEST)
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_recover_response(response)

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 4
        assert error.args[1] == response.status_code
        assert error.args[3] == upload_mod.PERMANENT_REDIRECT
        # Make sure still invalid.
        assert upload.invalid

    def test__process_recover_response_no_range(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True
        upload._stream = mock.Mock(spec=[u'seek'])
        upload._bytes_uploaded = mock.sentinel.not_zero
        assert upload.bytes_uploaded != 0

        response = _make_response(
            status_code=upload_mod.PERMANENT_REDIRECT, headers={})
        ret_val = upload._process_recover_response(response)
        assert ret_val is None
        # Check the state of ``upload`` after.
        assert upload.bytes_uploaded == 0
        assert not upload.invalid
        upload._stream.seek.assert_called_once_with(0)

    def test__process_recover_response_bad_range(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True
        upload._stream = mock.Mock(spec=[u'seek'])
        upload._bytes_uploaded = mock.sentinel.not_zero

        headers = {u'range': u'bites=9-11'}
        response = _make_response(
            status_code=upload_mod.PERMANENT_REDIRECT, headers=headers)
        with pytest.raises(exceptions.InvalidResponse) as exc_info:
            upload._process_recover_response(response)

        error = exc_info.value
        assert error.response is response
        assert len(error.args) == 3
        assert error.args[1] == headers[u'range']
        # Check the state of ``upload`` after (untouched).
        assert upload.bytes_uploaded is mock.sentinel.not_zero
        assert upload.invalid
        upload._stream.seek.assert_not_called()

    def test__process_recover_response_with_range(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True
        upload._stream = mock.Mock(spec=[u'seek'])
        upload._bytes_uploaded = mock.sentinel.not_zero
        assert upload.bytes_uploaded != 0

        end = 11
        headers = {u'range': u'bytes=0-{:d}'.format(end)}
        response = _make_response(
            status_code=upload_mod.PERMANENT_REDIRECT, headers=headers)
        ret_val = upload._process_recover_response(response)
        assert ret_val is None
        # Check the state of ``upload`` after.
        assert upload.bytes_uploaded == end + 1
        assert not upload.invalid
        upload._stream.seek.assert_called_once_with(end + 1)

    def test_recover(self):
        upload = upload_mod.ResumableUpload(RESUMABLE_URL, ONE_MB)
        upload._invalid = True  # Make sure invalid.
        upload._stream = mock.Mock(spec=[u'seek'])
        upload._resumable_url = u'http://test.invalid?upload_id=big-deal'

        end = 55555
        headers = {u'range': u'bytes=0-{:d}'.format(end)}
        transport = self._chunk_mock(
            upload_mod.PERMANENT_REDIRECT, headers)

        ret_val = upload.recover(transport)
        assert ret_val is transport.request.return_value
        # Check the state of ``upload`` after.
        assert upload.bytes_uploaded == end + 1
        assert not upload.invalid
        upload._stream.seek.assert_called_once_with(end + 1)
        expected_headers = {u'content-range': u'bytes */*'}
        transport.request.assert_called_once_with(
            u'PUT', upload.resumable_url, data=None, headers=expected_headers)


@mock.patch(u'random.randrange', return_value=1234567890123456789)
def test__get_boundary(mock_rand):
    result = upload_mod._get_boundary()
    assert result == b'===============1234567890123456789=='
    mock_rand.assert_called_once_with(sys.maxsize)


class Test__construct_multipart_request(object):

    @mock.patch(u'google.resumable_media.upload._get_boundary',
                return_value=b'==1==')
    def test_binary(self, mock_get_boundary):
        data = b'By nary day tuh'
        metadata = {u'name': u'hi-file.bin'}
        content_type = u'application/octet-stream'
        payload, multipart_boundary = upload_mod._construct_multipart_request(
            data, metadata, content_type)

        assert multipart_boundary == mock_get_boundary.return_value
        expected_payload = (
            b'--==1==\r\n' +
            JSON_TYPE_LINE +
            b'\r\n'
            b'{"name": "hi-file.bin"}\r\n'
            b'--==1==\r\n'
            b'content-type: application/octet-stream\r\n'
            b'\r\n'
            b'By nary day tuh\r\n'
            b'--==1==--')
        assert payload == expected_payload
        mock_get_boundary.assert_called_once_with()

    @mock.patch(u'google.resumable_media.upload._get_boundary',
                return_value=b'==2==')
    def test_unicode(self, mock_get_boundary):
        data_unicode = u'\N{snowman}'
        # _construct_multipart_request ASSUMES callers pass bytes.
        data = data_unicode.encode(u'utf-8')
        metadata = {u'name': u'snowman.txt'}
        content_type = BASIC_CONTENT
        payload, multipart_boundary = upload_mod._construct_multipart_request(
            data, metadata, content_type)

        assert multipart_boundary == mock_get_boundary.return_value
        expected_payload = (
            b'--==2==\r\n' +
            JSON_TYPE_LINE +
            b'\r\n'
            b'{"name": "snowman.txt"}\r\n'
            b'--==2==\r\n'
            b'content-type: text/plain\r\n'
            b'\r\n'
            b'\xe2\x98\x83\r\n'
            b'--==2==--')
        assert payload == expected_payload
        mock_get_boundary.assert_called_once_with()


def test__get_total_bytes():
    data = b'some data'
    stream = io.BytesIO(data)
    # Check position before function call.
    assert stream.tell() == 0
    assert upload_mod._get_total_bytes(stream) == len(data)
    # Check position after function call.
    assert stream.tell() == 0

    # Make sure this works just as well when not at beginning.
    curr_pos = 3
    stream.seek(curr_pos)
    assert upload_mod._get_total_bytes(stream) == len(data)
    # Check position after function call.
    assert stream.tell() == curr_pos


class Test__get_next_chunk(object):

    def test_exhausted(self):
        data = b'the end'
        stream = io.BytesIO(data)
        stream.seek(len(data))
        with pytest.raises(ValueError):
            upload_mod._get_next_chunk(stream, 1)

    def test_success(self):
        stream = io.BytesIO(b'0123456789')
        chunk_size = 3
        # Splits into 4 chunks: 012, 345, 678, 9
        result0 = upload_mod._get_next_chunk(stream, chunk_size)
        result1 = upload_mod._get_next_chunk(stream, chunk_size)
        result2 = upload_mod._get_next_chunk(stream, chunk_size)
        result3 = upload_mod._get_next_chunk(stream, chunk_size)
        assert result0 == (0, 2, b'012')
        assert result1 == (3, 5, b'345')
        assert result2 == (6, 8, b'678')
        assert result3 == (9, 9, b'9')
        assert stream.tell() == 10


def _make_response(status_code=http_client.OK, headers=None):
    headers = headers or {}
    return mock.Mock(
        headers=headers, status_code=status_code,
        spec=[u'headers', u'status_code'])
