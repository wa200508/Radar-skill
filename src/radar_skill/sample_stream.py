# Copyright (c) 2026 wa200508
# SPDX-License-Identifier: Apache-2.0
# Extracted from airsim-rf 3b36fe0; original MIT notice is retained in licenses/.
"""Receiver-facing cf32 stream; timestamps refer to the acquisition clock.

One producer and consumer per connection; receiver streams are multiplexed by ID.
A socket timeout or protocol failure aborts rather than dropping/resuming samples.
"""
from dataclasses import dataclass
import json
import math
import re
import select
import socket
import struct
import numpy as np

MAX_SAMPLES=2_000_000
MAX_HEADER=16_384
PREFIX=struct.Struct('!II')


def _exact(sock,count):
    data=bytearray(count);view=memoryview(data);offset=0
    while offset<count:
        n=sock.recv_into(view[offset:])
        if not n:raise EOFError('I/Q connection closed before explicit completion')
        offset+=n
    return data


def _send(sock,header,payload=b''):
    encoded=json.dumps({'version':2,**header},allow_nan=False,separators=(',',':')).encode()
    if len(encoded)>MAX_HEADER or len(payload)>8*MAX_SAMPLES:raise ValueError('I/Q frame exceeds transport limit')
    sock.sendall(PREFIX.pack(len(encoded),len(payload)))
    sock.sendall(encoded)
    if payload:sock.sendall(payload)


def _receive(sock):
    header_bytes,payload_bytes=PREFIX.unpack(_exact(sock,PREFIX.size))
    if not 0<header_bytes<=MAX_HEADER or payload_bytes>8*MAX_SAMPLES:
        raise ValueError('Invalid I/Q frame lengths')
    header=json.loads(_exact(sock,header_bytes))
    if not isinstance(header,dict) or not _integer(header.get('version')) or header.get('version') not in (1,2):raise ValueError('Unsupported I/Q protocol version')
    # Version 1 is decoded only at this legacy wire boundary.
    if header.get('version')==1 and header.get('type')=='iq':
        header['timestamp_ns']=header.pop('sim_time_ns',None)
    # Validate the bounded header before reading any data payload.
    kind=header.get('type')
    if kind=='iq':
        allowed={'version','type','sequence','receiver','sample_start','samples','timestamp_ns','sample_rate_hz',
            'carrier_hz','clipped_component_fraction','dtype','units'}
        if set(header)!=allowed:raise ValueError('Unexpected sample metadata fields')
        _validate_header(header)
        if payload_bytes!=header['samples']*8:raise ValueError('I/Q payload size differs from sample count')
    elif kind in ('end','ack','window_end','window_ack'):
        if payload_bytes:raise ValueError('Control frames cannot contain samples')
    else:raise ValueError('Unknown I/Q frame type')
    return header,_exact(sock,payload_bytes)


def _integer(value):return isinstance(value,int) and not isinstance(value,bool)


def _validate_header(h):
    if not isinstance(h.get('receiver'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',h['receiver']):
        raise ValueError('Invalid receiver ID')
    for key in ('sequence','sample_start','samples','timestamp_ns'):
        if not _integer(h.get(key)):raise ValueError(f'{key} must be an integer')
    if h['sequence']<0 or h['sample_start']<0 or not 1<=h['samples']<=MAX_SAMPLES:
        raise ValueError('Invalid sequence, offset or sample count')
    for key in ('sample_rate_hz','carrier_hz','clipped_component_fraction'):
        value=h.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be finite')
    if h['sample_rate_hz']<=0 or h['carrier_hz']<=0 or not 0<=h['clipped_component_fraction']<=1:
        raise ValueError('Invalid sample rate, carrier or clipping fraction')
    if h.get('dtype')!='cf32_le' or h.get('units')!='volts':raise ValueError('Expected cf32_le complex volts')


def _advance(states,h):
    name=h['receiver'];previous=states.get(name)
    if previous is None:
        if h['sample_start']!=0:raise ValueError('First receiver sample offset must be zero')
    else:
        offset,epoch,rate,carrier=previous
        if h['sample_start']!=offset or h['timestamp_ns']!=epoch:
            raise ValueError('Receiver samples or timestamps are discontinuous')
        if (h['sample_rate_hz'],h['carrier_hz'])!=(rate,carrier):
            raise ValueError('Receiver rate/carrier changed within stream')
    duration=h['samples']*1e9/h['sample_rate_hz']
    if abs(duration-round(duration))>1e-4:raise ValueError('Window duration must align to integer nanoseconds')
    states[name]=(h['sample_start']+h['samples'],h['timestamp_ns']+round(duration),h['sample_rate_hz'],h['carrier_hz'])


@dataclass(frozen=True)
class SampleBlock:
    receiver: str
    samples: np.ndarray
    sample_start: int
    timestamp_ns: int
    sample_rate_hz: float
    carrier_hz: float
    clipped_component_fraction: float


class SampleStreamWriter:
    """Use send_samples with any receiver adapter; finish waits for consumer acknowledgement.

    The supplied socket needs a finite timeout. The caller owns socket closure.
    Any failure makes this connection unusable; do not retry frames on it.
    """
    def __init__(self,sock,*,carrier_hz,protocol_version=2):
        if sock.gettimeout() is None or sock.gettimeout()<=0:raise ValueError('A finite positive socket timeout is required')
        if not _integer(protocol_version) or protocol_version not in (1,2):raise ValueError('Unsupported protocol version')
        self.version=protocol_version
        self.sock=sock;self.carrier_hz=carrier_hz;self.states={};self.sequence=0;self.closed=False;self.epochs={};self.committed_sequence=0

    def send_samples(self,receiver,samples,*,timestamp_ns,sample_rate_hz,clipped_component_fraction=0.):
        if self.closed:raise RuntimeError('I/Q stream is closed')
        samples=np.asarray(samples,dtype='<c8')
        if samples.ndim!=1 or not np.isfinite(samples).all():raise ValueError('Finite one-dimensional samples required')
        h={'type':'iq','sequence':self.sequence,'receiver':receiver,
            'sample_start':self.states.get(receiver,(0,))[0], 'samples':len(samples),
            'timestamp_ns':timestamp_ns,'sample_rate_hz':sample_rate_hz,
            'carrier_hz':self.carrier_hz,'clipped_component_fraction':clipped_component_fraction,
            'dtype':'cf32_le','units':'volts'}
        try:
            _validate_header(h);_advance(self.states,h)
            self.epochs[receiver]=timestamp_ns
            if self.version==1:h['sim_time_ns']=h.pop('timestamp_ns')
            _send(self.sock,{'version':self.version,**h},samples.tobytes())
            self.sequence+=1
        except BaseException:
            self.closed=True
            raise

    def commit_window(self):
        """Bounded application backpressure, independent of any processing algorithm.

        The receiver accepts this group only after its preceding sample handlers
        return. Failure/disconnect leaves the group unacknowledged.
        """
        if self.closed:raise RuntimeError('I/Q stream is closed')
        if self.version!=2:raise ValueError('Window commits require sample-stream version 2')
        if self.sequence==self.committed_sequence or len(set(self.epochs.values()))!=1:
            raise ValueError('Commit a nonempty coherent receiver window')
        epoch=next(iter(self.epochs.values()))
        totals={name:state[0] for name,state in self.states.items()}
        expected={'version':2,'type':'window_ack','sequence':self.sequence,'timestamp_ns':epoch,'sample_totals':totals}
        try:
            _send(self.sock,{**expected,'type':'window_end'})
            ack,_=_receive(self.sock)
            if ack!=expected:raise ValueError('Consumer did not acknowledge the coherent window')
            self.committed_sequence=self.sequence
        except BaseException:
            self.closed=True
            raise
        return epoch

    def abort(self):
        self.closed=True
        try:self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass

    def finish(self):
        if self.closed:raise RuntimeError('I/Q stream is closed')
        self.closed=True
        totals={name:state[0] for name,state in self.states.items()}
        _send(self.sock,{'version':self.version,'type':'end','sequence':self.sequence,'sample_totals':totals})
        ack,_=_receive(self.sock)
        if ack!={'version':self.version,'type':'ack','sequence':self.sequence,'sample_totals':totals}:
            raise ValueError('Consumer did not acknowledge the complete sample stream')
        return totals


def iter_stream(sock, *, validate_end=None, validate_window=None, allow_idle=False):
    """Yield complete voltage blocks; acknowledge only after consumer exhausts them.

    Breaking iteration early or raising in processing leaves completion
    unacknowledged. EOF without an end frame is a failure, not normal completion.
    Optional validate_end runs before ACK to enforce application-level completion.

    allow_idle waits without a deadline BETWEEN frames, for paused or sporadic
    acquisition sources. EOF still fails immediately. Once any frame bytes arrive,
    the socket's finite timeout still bounds incomplete headers and payloads.
    Processing acknowledgements retain the writer's finite timeout.
    """
    if sock.gettimeout() is None or sock.gettimeout()<=0:raise ValueError('A finite positive socket timeout is required')
    states={};sequence=0;version=None;last_window_sequence=0;epochs={}
    while True:
        if allow_idle:select.select([sock],[],[])
        h,payload=_receive(sock)
        if version is None:version=h['version']
        if h['version']!=version:raise ValueError('Protocol version changed within stream')
        if not _integer(h.get('sequence')) or h['sequence']!=sequence:raise ValueError('I/Q sequence mismatch')
        if h['type']=='window_end':
            if version!=2 or sequence==last_window_sequence or len(set(epochs.values()))!=1:
                raise ValueError('Invalid coherent window commit')
            totals={name:state[0] for name,state in states.items()}
            if h.get('sample_totals')!=totals or h.get('timestamp_ns')!=next(iter(epochs.values())):
                raise ValueError('Window commit totals or timestamp mismatch')
            if validate_window is not None:validate_window()
            _send(sock,{**h,'type':'window_ack'})
            last_window_sequence=sequence
            continue
        if h['type']=='end':
            totals={name:state[0] for name,state in states.items()}
            if h.get('sample_totals')!=totals:raise ValueError('I/Q completion totals mismatch')
            if validate_end is not None:validate_end()
            _send(sock,{'version':version,'type':'ack','sequence':sequence,'sample_totals':totals})
            return
        if h['type']!='iq':raise ValueError('Unexpected I/Q control frame')
        _advance(states,h);epochs[h['receiver']]=h['timestamp_ns']
        samples=np.frombuffer(payload,dtype='<c8').copy()
        if not np.isfinite(samples).all():raise ValueError('Nonfinite I/Q samples')
        sequence+=1
        yield SampleBlock(h['receiver'],samples,h['sample_start'],h['timestamp_ns'],
            h['sample_rate_hz'],h['carrier_hz'],h['clipped_component_fraction'])
