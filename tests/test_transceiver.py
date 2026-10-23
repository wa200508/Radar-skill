"""Active skill issues real control requests to a neutral radio adapter."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import pytest
from radar_skill.app import RadarSkill
from radar_skill.transceiver import validate_mode, configuration_id

ROOT=Path(__file__).resolve().parents[1]


def test_skill_configures_waveform_enables_tx_and_rx_and_stops():
    received=[]
    class Adapter(BaseHTTPRequestHandler):
        def handle_command(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.command,self.path,payload))
            if self.path=='/v1/configuration':body={'configuration_id':configuration_id(payload)}
            else:body={**payload,'configuration_id':configuration_id(received[0][2])}
            encoded=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(encoded)))
            self.end_headers();self.wfile.write(encoded)
        do_PUT=handle_command
        do_POST=handle_command
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Adapter)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    skill=RadarSkill(ROOT/'examples/receiver-config.json')
    mode=json.loads((ROOT/'examples/radar-mode.json').read_text())
    try:
        skill.start_transceiver(f'http://127.0.0.1:{server.server_port}',mode,ready_timeout=1)
        assert received[0]==('PUT','/v1/configuration',mode)
        assert received[1]==('POST','/v1/state',{'transmit':True,'receive':True})
        assert skill.snapshot()['transceiver']['transmit']
        skill.set_transceiver_state({'transmit':False,'receive':True})
        assert received[-1][2]=={'transmit':False,'receive':True}
        assert not skill.snapshot()['transceiver']['transmit']
        skill.stop_transceiver()
        assert received[-1][2]=={'transmit':False,'receive':False}
        assert not skill.snapshot()['transceiver']['transmit']
    finally:server.shutdown();server.server_close()


@pytest.mark.parametrize('alteration',('gate','rate','unknown','nonfinite','timing','channel'))
def test_invalid_transmit_receive_modes_are_rejected(alteration):
    config=json.loads((ROOT/'examples/receiver-config.json').read_text())
    mode=json.loads((ROOT/'examples/radar-mode.json').read_text())
    if alteration=='gate':mode['receiver']['num_samples']=1000
    elif alteration=='rate':mode['receiver']['sample_rate_hz']=20e6
    elif alteration=='unknown':mode['waveform']['target_position']=[1,2,3]
    elif alteration=='nonfinite':mode['waveform']['power_dbm']=float('nan')
    elif alteration=='timing':mode['waveform']['pri_s']=20e-6
    else:mode['receiver']['channel']='other'
    with pytest.raises(ValueError):validate_mode(mode,config)
