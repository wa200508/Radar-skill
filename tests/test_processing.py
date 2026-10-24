import json
from pathlib import Path
import numpy as np
import pytest
from radar_skill.processing import range_doppler


def config():return json.loads((Path(__file__).resolve().parents[1]/'examples/receiver-config.json').read_text())


def samples(c,distance,velocity):
    a=c['acquisition'];fs=c['sample_rate_hz'];fc=c['carrier_hz'];slope=a['bandwidth_hz']/a['chirp_duration_s'];fd=2*velocity*fc/299792458.
    t=np.arange(a['num_samples'])/fs+a['adc_start_s'];slow=np.arange(100)*a['pri_s'];cube=np.zeros((100,400),np.complex64)
    cube[:,30:286]=.05*np.exp(2j*np.pi*((2*distance*slope/299792458.+fd)*t[None,:]+fd*slow[:,None]))
    return cube.ravel()


@pytest.mark.parametrize('distance,velocity',[(50,0),(125,15),(300,-20)])
def test_range_and_signed_range_rate(distance,velocity):
    c=config();rd=range_doppler(samples(c,distance,velocity),c['sample_rate_hz'],c['carrier_hz'],c['acquisition']);d=rd['detection']
    assert d['detected']
    assert d['range_m']==pytest.approx(distance,abs=rd['range_resolution_m'])
    assert d['range_rate_m_s']==pytest.approx(velocity,abs=rd['velocity_resolution_m_s'])


def test_noise_only_and_empty_signal_have_no_candidate():
    c=config();rng=np.random.default_rng(3)
    for values in (rng.normal(size=40000)+1j*rng.normal(size=40000),np.zeros(40000,np.complex64)):
        assert not range_doppler(values,c['sample_rate_hz'],c['carrier_hz'],c['acquisition'])['detection']['detected']


def test_incomplete_chirps_are_rejected():
    c=config()
    with pytest.raises(ValueError,match='complete'):range_doppler(np.ones(39999),c['sample_rate_hz'],c['carrier_hz'],c['acquisition'])



def test_display_matrix_matches_axes_and_does_not_clip_weak_values():
    c=config();rd=range_doppler(samples(c,125,15),c['sample_rate_hz'],c['carrier_hz'],c['acquisition'])
    values=np.asarray(rd['power_db']);assert values.shape==(len(rd['range_m']),len(rd['doppler_khz']))
    assert rd['power_axes']==['range','doppler']
    row,column=np.unravel_index(values.argmax(),values.shape)
    assert rd['range_m'][row]==pytest.approx(125,abs=rd['range_resolution_m'])
    assert rd['range_rate_m_s'][column]==pytest.approx(15,abs=rd['velocity_resolution_m_s'])
    assert rd['doppler_khz'][column]>0 and np.min(values)<-20


def test_fft_bin_power_has_a_defined_voltage_reference():
    c=config();a=c['acquisition'];cube=np.zeros((100,400),complex)
    phase=2*np.pi*(32*np.arange(256)[None,:]/256+8*np.arange(100)[:,None]/64)
    cube[:,30:286]=.05*np.exp(1j*phase)
    rd=range_doppler(cube.ravel(),c['sample_rate_hz'],c['carrier_hz'],a)
    assert rd['peak_bin_power_dbv2']==pytest.approx(20*np.log10(.05),abs=1e-6)
