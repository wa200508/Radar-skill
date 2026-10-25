"""Sparse acquisition may idle; partial transfers, EOF and bad ACKs still fail."""
from concurrent.futures import ThreadPoolExecutor
import socket
import time
import numpy as np
import pytest
from radar_skill.sample_stream import SampleStreamWriter,iter_stream


def test_idle_between_committed_windows_does_not_expire_transfer_timeout():
    source,receiver=socket.socketpair();source.settimeout(2);receiver.settimeout(.02)
    with source,receiver,ThreadPoolExecutor(1) as pool:
        reader=pool.submit(lambda:list(iter_stream(receiver,allow_idle=True)))
        writer=SampleStreamWriter(source,carrier_hz=915e6)
        # Includes READY/operator wait before the first window and a later pause.
        for epoch in (0,1000):
            time.sleep(.08)
            assert not reader.done()
            writer.send_samples('rx',np.array([1+2j,3-4j]),timestamp_ns=epoch,sample_rate_hz=2e6)
            assert writer.commit_window()==epoch
        writer.finish();blocks=reader.result(timeout=2)
    assert [b.timestamp_ns for b in blocks]==[0,1000]
    assert [b.sample_start for b in blocks]==[0,2]
    np.testing.assert_array_equal(blocks[0].samples,blocks[1].samples)


def test_idle_permission_does_not_hide_incomplete_frame_timeout():
    source,receiver=socket.socketpair();source.settimeout(2);receiver.settimeout(.02)
    with source,receiver:
        source.sendall(b'\0')  # Only the first byte of the frame prefix arrives.
        with pytest.raises(TimeoutError):next(iter_stream(receiver,allow_idle=True))


def test_disconnect_wakes_idle_receiver_and_is_not_clean_completion():
    source,receiver=socket.socketpair();source.settimeout(2);receiver.settimeout(.02)
    with receiver,ThreadPoolExecutor(1) as pool:
        reader=pool.submit(lambda:list(iter_stream(receiver,allow_idle=True)))
        try:
            time.sleep(.08);assert not reader.done()
        finally:source.close()
        with pytest.raises(EOFError,match='explicit completion'):reader.result(timeout=2)


def test_default_still_bounds_idle_wait():
    source,receiver=socket.socketpair();source.settimeout(2);receiver.settimeout(.02)
    with source,receiver:
        with pytest.raises(TimeoutError):next(iter_stream(receiver))


def test_missing_processing_ack_still_times_out():
    source,receiver=socket.socketpair();source.settimeout(.02);receiver.settimeout(2)
    with source,receiver:
        writer=SampleStreamWriter(source,carrier_hz=915e6)
        writer.send_samples('rx',np.ones(2),timestamp_ns=0,sample_rate_hz=2e6)
        with pytest.raises(TimeoutError):writer.commit_window()
        assert writer.closed
