# SPDX-License-Identifier: Apache-2.0
"""Skill-owned display description. Bindings refer only to received-I/Q results.

The dashboard renders these primitives; it never imports this module or decides
which algorithm/metrics a skill should present. No executable UI code is sent.
"""


def describe_display(arrays):
    sites=arrays['sites']
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
            {'label':'Detection','value':{'path':'/radar/detection/detected'},'true_label':'DETECTED','false_label':'NO DETECTION'},
            {'label':'Range','value':{'path':'/radar/detection/range_m'},'unit':'m','precision':1,'valid_when':{'path':'/radar/detection/detected'}},
            {'label':'Range rate','value':{'path':'/radar/detection/range_rate_m_s'},'unit':'m/s','precision':1,'valid_when':{'path':'/radar/detection/detected'}}],
         'caption':'One strongest candidate; 18 dB contrast above median map power. This is not calibrated CFAR.'},
        {'id':'range-doppler','type':'heatmap','title':'Range–Doppler map',
         'x':{'data':{'path':'/radar/range_m'},'label':'Range','unit':'m'},
         'y':{'data':{'path':'/radar/range_rate_m_s'},'label':'Range rate','unit':'m/s'},
         'values':{'path':'/radar/power_db'},'color':{'label':'Contrast above median','unit':'dB','range':[-10,35]},
         'caption':'64 chirps · Hann-windowed FFT · positive range rate is receding. The map retains FMCW range/Doppler coupling; candidate range is corrected.'}]
    return {'schema_version':1,'id':'radar-rf-skill','revision':'1','title':'Radar RF Skill',
            'selectors':selectors,'panels':panels,'histories':[{
                'key':'waterfall','limit':60,'row':{'timestamp_ns':{'path':'/timestamp_ns'},
                'psd_db':{'path':'/psd/density_db_v2_hz'},
                'site_psd_db':{'path':'/receiver_spectra','each':'/psd/density_db_v2_hz'}}}]}
