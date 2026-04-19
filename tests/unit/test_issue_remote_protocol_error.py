
import pytest
import asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from kiro.streaming_core import collect_stream_to_result, StreamResult

@pytest.mark.asyncio
async def test_collect_stream_retry_on_remote_protocol_error():
    """
    Test that collect_stream_to_result retries when a RemoteProtocolError occurs mid-stream.
    """
    # Setup: First response fails mid-stream, second one succeeds
    mock_response_fail = AsyncMock(spec=httpx.Response)
    mock_response_fail.status_code = 200
    mock_response_fail.aclose = AsyncMock()
    
    # Simulate mid-stream failure
    async def fail_iterator():
        yield b"event: content\ndata: \"Partial content\"\n\n"
        # Simulate RemoteProtocolError or similar
        raise Exception("peer closed connection without sending complete message body (incomplete chunked read)")
    
    mock_response_fail.aiter_bytes.return_value = fail_iterator()
    
    mock_response_success = AsyncMock(spec=httpx.Response)
    mock_response_success.status_code = 200
    mock_response_success.aclose = AsyncMock()
    
    async def success_iterator():
        # Using a simplified format that our parser can handle
        # For simplicity in this test, we can mock parse_kiro_stream instead of raw bytes
        # or use raw bytes that match AwsEventStreamParser logic.
        # But here we are testing collect_stream_to_result's retry loop.
        yield b"chunk" 
    
    mock_response_success.aiter_bytes.return_value = success_iterator()
    
    # Mock make_request factory
    make_request_calls = 0
    async def mock_make_request():
        nonlocal make_request_calls
        make_request_calls += 1
        if make_request_calls == 1:
            return mock_response_fail
        return mock_response_success
    
    # Mock parse_kiro_stream and other internal bits to focus on the retry loop
    mock_event_success = MagicMock()
    mock_event_success.type = "content"
    mock_event_success.content = "Full content"
    
    async def mock_parse_kiro_stream(response, timeout, enable_thinking):
        if response == mock_response_fail:
            # Replicate the Exception from parse_kiro_stream
            raise Exception("peer closed connection without sending complete message body (incomplete chunked read)")
        yield mock_event_success

    with patch('kiro.streaming_core.parse_kiro_stream', side_effect=mock_parse_kiro_stream):
        with patch('kiro.streaming_core.parse_bracket_tool_calls', return_value=[]):
            result = await collect_stream_to_result(mock_make_request, max_retries=2)
    
    assert make_request_calls == 2
    assert result.content == "Full content"
    assert mock_response_fail.aclose.called
    assert mock_response_success.aclose.called

@pytest.mark.asyncio
async def test_collect_stream_exhaust_retries():
    """
    Test that collect_stream_to_result eventually raises if retries are exhausted.
    """
    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.aclose = AsyncMock()
    
    async def mock_make_request():
        return mock_response

    async def mock_parse_kiro_stream(response, timeout, enable_thinking):
        if False: yield
        raise Exception("peer closed connection without sending complete message body (incomplete chunked read)")

    with patch('kiro.streaming_core.parse_kiro_stream', side_effect=mock_parse_kiro_stream):
        with pytest.raises(Exception) as excinfo:
            await collect_stream_to_result(mock_make_request, max_retries=2)
        
        assert "incomplete chunked read" in str(excinfo.value)
