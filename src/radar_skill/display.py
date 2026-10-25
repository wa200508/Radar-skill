# SPDX-License-Identifier: Apache-2.0
"""Skill-owned display description. Bindings refer only to received-I/Q results.

The dashboard renders these primitives; it never imports this module or decides
which algorithm/metrics a skill should present. No executable UI code is sent.
"""


def describe_display(arrays):
    sites=arrays['sites'];acquisition=arrays['acquisition']
    doppler_limit_khz=.5/acquisition['pri_s']/1000
    slope=acquisition['bandwidth_hz']/acquisition['chirp_duration_s']
    range_limit_m=arrays['sample_rate_hz']*299792458./(4*slope)
    names=[site['id'] for site in sites]
    selectors=[{'id':'receiver','label':'Receiver I/Q stream','options':[
        {'value':str(i),'label':name+' · element 0','available':{'path':f'/receiver_spectra/{i}/psd/frequency_khz'}}
        for i,name in enumerate(names)]}]
    panels=[
        {'id':'psd','type':'line','title':'Radar receiver I/Q spectrum',
         'x':{'data':{'path':'/receiver_spectra/{receiver}/psd/frequency_khz'},'label':'Beat-frequency offset','unit':'kHz'},
         'y':{'label':'PSD','unit':'dB re 1 V²/Hz','range':[-120,-30]},
         'series':{'path':'/receiver_spectra/{receiver}/psd/density_db_v2_hz'},
         'caption':'Dechirped, bias-subtracted ADC volts. Zero-filled gate gaps retain PRI timing.'},
        {'id':'waterfall','type':'waterfall','title':'Radar receiver I/Q waterfall',
         'x':{'data':{'path':'/receiver_spectra/{receiver}/psd/frequency_khz'},'label':'Beat-frequency offset','unit':'kHz'},
         'y':{'label':'Acquisition windows · newest at top'},
         'rows':{'path':'/waterfall','each':'/site_psd_db/{receiver}'},
         'color':{'label':'PSD','unit':'dB re 1 V²/Hz','range':[-120,-45]}},
        {'id':'detection','type':'metrics','title':'Radar candidate','items':[
            {'label':'Median FFT-bin power','value':{'path':'/radar/median_bin_power_dbv2'},'unit':'dB re 1 V²','precision':1},
            {'label':'Peak FFT-bin power','value':{'path':'/radar/peak_bin_power_dbv2'},'unit':'dB re 1 V²','precision':1},
            {'label':'Detection','value':{'path':'/radar/detection/detected'},'true_label':'DETECTED','false_label':'NO DETECTION'},
            {'label':'Range','value':{'path':'/radar/detection/range_m'},'unit':'m','precision':1,'valid_when':{'path':'/radar/detection/detected'}},
            {'label':'Range rate','value':{'path':'/radar/detection/range_rate_m_s'},'unit':'m/s','precision':1,'valid_when':{'path':'/radar/detection/detected'}}],
         'caption':'One strongest candidate; 18 dB contrast above median map power. This is not calibrated CFAR.'},
        {'id':'range-doppler','type':'heatmap','title':'Range–Doppler map',
         'x':{'data':{'path':'/radar/doppler_khz'},'label':'Doppler','unit':'kHz','range':[-doppler_limit_khz,doppler_limit_khz]},
         'y':{'data':{'path':'/radar/range_m'},'label':'Range','unit':'m','range':[0,range_limit_m]},
         'values':{'path':'/radar/power_db'},'color':{'label':'Contrast above median','unit':'dB','range':[-5,15],
            'presets':[{'label':'Narrow color scale (−5 to +15 dB)','range':[-5,15]},
                       {'label':'Medium color scale (−10 to +45 dB)','range':[-10,45]},
                       {'label':'Wide color scale (−30 to +45 dB)','range':[-30,45]}]},
         'caption':'Every color scale shows the same received I/Q; none isolates or removes noise, clutter or target echoes. Range increases upward; full signed Doppler spectrum. Positive Doppler is receding. Color is contrast to the current map median, not calibrated SNR; scale changes never alter I/Q or detection. The map retains FMCW range/Doppler coupling.'}]
    return {'schema_version':1,'id':'radar-rf-skill','revision':'3','title':'Radar RF Skill',
            'selectors':selectors,'panels':panels,'histories':[{
                'key':'waterfall','limit':60,'row':{'timestamp_ns':{'path':'/timestamp_ns'},
                'psd_db':{'path':'/psd/density_db_v2_hz'},
                'site_psd_db':{'path':'/receiver_spectra','each':'/psd/density_db_v2_hz'}}}]}
