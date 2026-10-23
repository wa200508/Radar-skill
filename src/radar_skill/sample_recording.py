# Copyright (c) 2026 wa200508
# SPDX-License-Identifier: Apache-2.0
# Extracted from airsim-rf 3b36fe0; original MIT notice is retained in licenses/.
"""Source-neutral, lossless cf32 SigMF archives and TCP v2 sample replay.

Requires NumPy only. No simulation, propagation or receiver algorithm imports.
The JSONL journal publishes complete coherent windows; raw data is append-only.
"""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
import math
from pathlib import Path
import socket
import time

import numpy as np
from .sample_stream import SampleBlock, SampleStreamWriter, _validate_header, _advance

FORMAT = 'airsim-rf-sample-recording-v1'


def _json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False) + '\n')
    temporary.replace(path)


def _header(block, sequence=0):
    return {'receiver': block.receiver, 'sequence': sequence, 'sample_start': block.sample_start,
        'samples': len(block.samples), 'timestamp_ns': block.timestamp_ns,
        'sample_rate_hz': block.sample_rate_hz, 'carrier_hz': block.carrier_hz,
        'clipped_component_fraction': block.clipped_component_fraction,
        'dtype': 'cf32_le', 'units': 'volts'}


class SampleRecordingWriter:
    """Append exact receiver-facing float32 voltages, one coherent group at a time.

    Caller owns close(), including on failures. A failed write poisons the writer;
    only fully journaled windows are recoverable with explicit partial reading.
    """
    def __init__(self, directory, receiver_config):
        self.path = Path(directory)
        self.channels = [name for site in receiver_config['sites'] for name in site['channels']]
        rate = receiver_config['sample_rate_hz']; carrier = receiver_config['carrier_hz']
        if not self.channels or len(set(self.channels)) != len(self.channels):
            raise ValueError('Unique receiver channels required')
        for name in self.channels:
            _validate_header(_header(SampleBlock(name, np.zeros(1), 0, 0, rate, carrier, 0.)))
        self.path.mkdir(parents=True, exist_ok=False)
        self.manifest = {'format': FORMAT, 'status': 'recording', 'channels': self.channels,
            'sample_rate_hz': rate, 'carrier_hz': carrier, 'units': 'volts',
            'datatype': 'cf32_le', 'windows': 0, 'samples_per_channel': 0,
            'payload_bytes': 0}
        self.voltage_reference=receiver_config.get('voltage_reference','input-referred complex volts')
        self.states = {}; self.closed = False; self.failed = False
        self.resources = ExitStack()
        try:
            self.files = {name: self.resources.enter_context((self.path / f'{name}.sigmf-data').open('xb'))
                for name in self.channels}
            self.journal = self.resources.enter_context((self.path / 'windows.jsonl').open('x'))
            _json(self.path / 'receiver-config.json', receiver_config)
            for name in self.channels:
                _json(self.path / f'{name}.sigmf-meta', self._metadata(name, []))
            _json(self.path / 'manifest.json', self.manifest)
        except BaseException:
            self.resources.close(); raise

    def _metadata(self, name, captures):
        return {'global': {'core:datatype': 'cf32_le', 'core:sample_rate': self.manifest['sample_rate_hz'],
            'core:version': '1.2.6', 'core:recorder': 'airsim-rf sample recorder',
            'core:description': f'{name}: {self.voltage_reference}; coherent group timing in windows.jsonl'},
            'captures': captures, 'annotations': []}

    def write_window(self, blocks):
        if self.closed or self.failed: raise RuntimeError('Sample recording is closed or failed')
        blocks = list(blocks)
        if [b.receiver for b in blocks] != self.channels:
            raise ValueError('Supply every configured channel once, in receiver configuration order')
        if len({(b.timestamp_ns, len(b.samples), b.sample_rate_hz, b.carrier_hz) for b in blocks}) != 1:
            raise ValueError('Recording requires coherent channel windows')
        states = dict(self.states); rows = []; payloads = []
        for block in blocks:
            samples = np.asarray(block.samples, dtype='<c8')
            if samples.ndim != 1 or not np.isfinite(samples).all(): raise ValueError('Finite 1-D samples required')
            h = _header(block); _validate_header(h); _advance(states, h)
            if (block.sample_rate_hz, block.carrier_hz) != (self.manifest['sample_rate_hz'], self.manifest['carrier_hz']):
                raise ValueError('Samples differ from receiver configuration rate/carrier')
            payload = samples.tobytes(); payloads.append(payload)
            rows.append({'receiver': block.receiver, 'clipped_component_fraction': block.clipped_component_fraction,
                'sha256': sha256(payload).hexdigest()})
        first = blocks[0]
        row = {'timestamp_ns': first.timestamp_ns, 'sample_start': first.sample_start,
            'samples': len(first.samples), 'channels': rows}
        try:
            for name, payload in zip(self.channels, payloads):
                self.files[name].write(payload); self.files[name].flush()
            self.journal.write(json.dumps(row, allow_nan=False) + '\n'); self.journal.flush()
            self.states = states
            self.manifest.update(windows=self.manifest['windows'] + 1,
                samples_per_channel=first.sample_start + len(first.samples),
                payload_bytes=self.manifest['payload_bytes'] + sum(map(len, payloads)))
            _json(self.path / 'manifest.json', self.manifest)
        except BaseException:
            self.failed = True; raise

    def close(self, status='complete'):
        if self.closed: return
        if status not in ('complete', 'stopped', 'failed'): raise ValueError('Invalid recording status')
        self.closed = True; self.resources.close()
        self.manifest['status'] = 'failed' if self.failed else status
        # Write standard SigMF capture offsets/frequencies once, not O(windows^2).
        captures = [{'core:sample_start': row['sample_start'], 'core:frequency': self.manifest['carrier_hz']}
            for row in _rows(self.path, self.manifest)]
        for name in self.channels: _json(self.path / f'{name}.sigmf-meta', self._metadata(name, captures))
        if self.manifest['windows']:
            from .gnuradio_export import write_flowgraphs
            write_flowgraphs(self.path, self.manifest)
        _json(self.path / 'manifest.json', self.manifest)


def _manifest(directory, allow_partial):
    root = Path(directory); manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('format') != FORMAT: raise ValueError('Expected a neutral sample recording, not a dashboard video archive or legacy capture')
    if manifest.get('status') not in ('complete', 'stopped') and not allow_partial:
        raise ValueError('Recording is incomplete; explicitly allow its committed prefix')
    if not manifest.get('windows'): raise ValueError('Recording has no committed sample windows')
    channels = manifest['channels']
    if not channels or len(set(channels)) != len(channels): raise ValueError('Invalid receiver channels')
    if manifest.get('datatype') != 'cf32_le' or manifest.get('units') != 'volts': raise ValueError('Expected cf32_le volts')
    for name in channels:
        _validate_header(_header(SampleBlock(name, np.zeros(1), 0, 0,
            manifest['sample_rate_hz'], manifest['carrier_hz'], 0.)))
        size = (root / f'{name}.sigmf-data').stat().st_size
        expected = manifest['samples_per_channel'] * 8
        if size < expected or (not allow_partial and size != expected): raise ValueError('Recording data byte count mismatch')
    if manifest['payload_bytes'] != manifest['samples_per_channel'] * len(channels) * 8:
        raise ValueError('Recording payload byte count mismatch')
    return root, manifest


def _rows(root, manifest):
    states = {}; total = 0
    with (root / 'windows.jsonl').open() as journal:
        for _ in range(manifest['windows']):
            line = journal.readline()
            if not line: raise ValueError('Truncated sample window journal')
            row = json.loads(line)
            if [c['receiver'] for c in row['channels']] != manifest['channels']: raise ValueError('Journal channel layout mismatch')
            for channel in row['channels']:
                h = {'sequence': 0, 'sample_start': row['sample_start'], 'samples': row['samples'],
                    'timestamp_ns': row['timestamp_ns'], 'sample_rate_hz': manifest['sample_rate_hz'],
                    'carrier_hz': manifest['carrier_hz'], 'receiver': channel['receiver'],
                    'clipped_component_fraction': channel['clipped_component_fraction'], 'dtype': 'cf32_le', 'units': 'volts'}
                _validate_header(h); _advance(states, h)
            total += row['samples']; yield row
    if total != manifest['samples_per_channel']: raise ValueError('Journal sample totals mismatch')


def iter_recorded_windows(directory, *, allow_partial=False, start_window=0, stop_window=None):
    """Yield tuples of neutral SampleBlocks. Memory is bounded by one window.

    Preserve original acquisition timestamps, phase, amplitude and channel order.
    File data/metadata must remain unchanged during reading. This is a reader for
    our coherent SigMF layout, not for every SigMF dataset.
    """
    root, manifest = _manifest(directory, allow_partial)
    stop = manifest['windows'] if stop_window is None else stop_window
    if not 0 <= start_window < stop <= manifest['windows']: raise ValueError('Invalid replay window range')
    with ExitStack() as stack:
        files = {name: stack.enter_context((root / f'{name}.sigmf-data').open('rb')) for name in manifest['channels']}
        for index, row in enumerate(_rows(root, manifest)):
            if index < start_window or index >= stop: continue
            blocks = []
            for channel in row['channels']:
                stream = files[channel['receiver']]; stream.seek(row['sample_start'] * 8)
                payload = stream.read(row['samples'] * 8)
                if len(payload) != row['samples'] * 8 or sha256(payload).hexdigest() != channel['sha256']:
                    raise ValueError('Sample data truncated or SHA-256 mismatch')
                samples = np.frombuffer(payload, dtype='<c8').copy()
                if not np.isfinite(samples).all(): raise ValueError('Nonfinite recorded samples')
                blocks.append(SampleBlock(channel['receiver'], samples, row['sample_start'], row['timestamp_ns'],
                    manifest['sample_rate_hz'], manifest['carrier_hz'], channel['clipped_component_fraction']))
            yield tuple(blocks)


def inspect(directory, *, verify=False, allow_partial=False):
    root, manifest = _manifest(directory, allow_partial)
    if verify:
        for _ in iter_recorded_windows(root, allow_partial=allow_partial): pass
    else:
        for _ in _rows(root, manifest): pass
    return {**manifest, 'signal_seconds': manifest['samples_per_channel'] / manifest['sample_rate_hz'],
        'sample_hashes_verified': verify}


def replay(directory, *, host='127.0.0.1', port=8811, speed=0., timeout=120.,
           allow_partial=False, start_window=0, stop_window=None):
    """Send to an unchanged skill using TCP v2 window commits/backpressure.

    speed=0 sends as fast as acknowledged; speed=1 paces original sample time.
    Subsets keep acquisition timestamps but TCP stream sample offsets restart at
    zero. A new replay needs a fresh consumer session/output directory.
    """
    if not math.isfinite(speed) or speed < 0 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Nonnegative finite speed and positive finite timeout required')
    _, manifest = _manifest(directory, allow_partial)
    started = time.monotonic(); origin = None; windows = 0
    with socket.create_connection((host, port), timeout=timeout) as connection:
        writer = SampleStreamWriter(connection, carrier_hz=manifest['carrier_hz'])
        for blocks in iter_recorded_windows(directory, allow_partial=allow_partial,
                start_window=start_window, stop_window=stop_window):
            if origin is None: origin = blocks[0].timestamp_ns; started = time.monotonic()
            if speed:
                target = started + (blocks[0].timestamp_ns - origin) / 1e9 / speed
                while (remaining := target - time.monotonic()) > 0: time.sleep(min(remaining, .5))
            for block in blocks:
                writer.send_samples(block.receiver, block.samples, timestamp_ns=block.timestamp_ns,
                    sample_rate_hz=block.sample_rate_hz, clipped_component_fraction=block.clipped_component_fraction)
            writer.commit_window(); windows += 1
        totals = writer.finish()
    return {'status': 'complete', 'windows': windows, 'sample_totals': totals,
        'wall_seconds': time.monotonic() - started, 'speed': speed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('inspect'); send = commands.add_parser('replay')
    export = commands.add_parser('gnuradio',help='Add stock-block GRC flowgraphs to an existing archive without copying I/Q')
    for command in (check, send, export):
        command.add_argument('recording', type=Path)
        command.add_argument('--allow-partial', action='store_true')
    check.add_argument('--verify', action='store_true')
    send.add_argument('--host', default='127.0.0.1'); send.add_argument('--port', type=int, default=8811)
    send.add_argument('--speed', type=float, default=0.); send.add_argument('--timeout', type=float, default=120.)
    send.add_argument('--start-window', type=int, default=0); send.add_argument('--stop-window', type=int)
    args = vars(parser.parse_args()); command = args.pop('command'); directory = args.pop('recording')
    try:
        if command=='gnuradio':
            from .gnuradio_export import write_flowgraphs
            manifest=inspect(directory,**args);write_flowgraphs(directory,manifest)
            result={'status':'complete','files':[str(directory/'replay.grc'),str(directory/'view.grc')]}
        else:result = (inspect if command == 'inspect' else replay)(directory, **args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
