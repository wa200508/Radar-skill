# SPDX-License-Identifier: Apache-2.0
"""Hardware-neutral FMCW commands. This module has no simulation interfaces.

The radio synthesizes the requested linear ramp in its RF hardware; the skill
specifies its waveform, TX power, coherent PRI, and complex ADC receive gate.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener


def configuration_id(configuration):
    return sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_mode(mode, arrays):
    """Validate the commanded mode against the configured receive stream."""
    if set(mode) != {'schema_version', 'waveform', 'receiver'} or type(mode['schema_version']) is not int or mode['schema_version'] != 1:
        raise ValueError('Transceiver configuration schema v1 required')
    waveform, receiver = mode['waveform'], mode['receiver']
    if set(waveform) != {'type', 'carrier_hz', 'bandwidth_hz', 'chirp_duration_s', 'pri_s', 'power_dbm'}:
        raise ValueError('Explicit linear FMCW waveform and TX power required')
    if waveform['type'] != 'fmcw-linear-up':
        raise ValueError('Only linear up-chirps are supported by this radar mode')
    if set(receiver) != {'channel', 'sample_rate_hz', 'num_samples', 'adc_start_s', 'gain_db'}:
        raise ValueError('Explicit complex ADC receiver gate and gain required')
    for parameters in (waveform, receiver):
        for key, value in parameters.items():
            if key in ('type', 'channel'): continue
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError('Non-finite or non-numeric radio parameter: '+key)
    if type(receiver['num_samples']) is not int or receiver['num_samples'] < 2:
        raise ValueError('ADC sample count must be an integer >= 2')
    if any(waveform[key] <= 0 for key in ('carrier_hz', 'bandwidth_hz', 'chirp_duration_s', 'pri_s')):
        raise ValueError('Waveform frequency and timing must be positive')
    fs = receiver['sample_rate_hz']
    if fs <= 0 or receiver['adc_start_s'] < 0 or receiver['adc_start_s']+receiver['num_samples']/fs > waveform['chirp_duration_s']:
        raise ValueError('Receive gate must fit inside the transmitted ramp')
    if waveform['pri_s'] < waveform['chirp_duration_s']:
        raise ValueError('Ramp must fit inside the PRI')
    for value in (fs*waveform['pri_s'], fs*receiver['adc_start_s']):
        if abs(value-round(value)) > 1e-6: raise ValueError('PRI and gate must align with ADC samples')
    names = [name for site in arrays['sites'] for name in site['channels']]
    if names != [receiver['channel']] or fs != arrays['sample_rate_hz'] or waveform['carrier_hz'] != arrays['carrier_hz']:
        raise ValueError('Commanded radio and receiver stream configuration disagree')
    for key in ('bandwidth_hz', 'chirp_duration_s', 'pri_s'):
        if waveform[key] != arrays['acquisition'][key]: raise ValueError('Commanded waveform and receive metadata disagree: '+key)
    for key in ('num_samples', 'adc_start_s'):
        if receiver[key] != arrays['acquisition'][key]: raise ValueError('Commanded ADC gate and receive metadata disagree: '+key)
    return deepcopy(mode)


class TransceiverClient:
    """Control a physical or simulated radio using the same HTTP contract."""
    def __init__(self, address, *, timeout=5):
        parsed = urlsplit(address)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('Absolute transceiver HTTP(S) URL required')
        self.address = address.rstrip('/'); self.timeout = timeout
        self.client = build_opener(ProxyHandler({}))
        self.configuration = None

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = Request(self.address+'/v1/'+path, data=data, method=method, headers={'Content-Type': 'application/json'})
        with self.client.open(request, timeout=self.timeout) as response:
            return json.load(response)

    def start(self, mode, *, ready_timeout=600):
        deadline = time.monotonic()+ready_timeout
        while True:
            try:
                result = self.request('PUT', 'configuration', mode)
                break
            except HTTPError:
                raise  # Unsupported settings must not turn into a silent fallback.
            except (URLError, TimeoutError):
                if time.monotonic() >= deadline: raise RuntimeError('Transceiver adapter did not become reachable')
                time.sleep(.1)
        if result.get('configuration_id') != configuration_id(mode):
            raise ValueError('Adapter acknowledged a different radar configuration')
        self.configuration = deepcopy(mode)
        return self.set_state(transmit=True, receive=True)

    def set_state(self, *, transmit, receive):
        result = self.request('POST', 'state', {'transmit': transmit, 'receive': receive})
        if result.get('transmit') is not transmit or result.get('receive') is not receive:
            raise ValueError('Adapter did not acknowledge requested TX/RX state')
        if self.configuration is not None and result.get('configuration_id')!=configuration_id(self.configuration):
            raise ValueError('Adapter changed the configured radar mode')
        return result

    def stop(self):
        return self.set_state(transmit=False, receive=False)
