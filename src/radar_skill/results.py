# Copyright (c) 2026 wa200508
# SPDX-License-Identifier: Apache-2.0
# Extracted from airsim-rf 3b36fe0; original MIT notice is retained in licenses/.
"""Application results journal. Contains no acquisition-source or truth knowledge."""
import json
from pathlib import Path


class ResultWriter:
    def __init__(self,directory):
        self.path=Path(directory);self.path.mkdir(parents=True,exist_ok=True)

    def write(self,result):
        epoch=result['timestamp_ns']
        if isinstance(epoch,bool) or not isinstance(epoch,int):raise ValueError('Integer acquisition timestamp required')
        rolling={item['key'] for item in result.get('display',{}).get('histories',[]) if isinstance(item.get('key'),str)}
        data={k:v for k,v in result.items() if k not in rolling|{'history','waterfall','updated_monotonic'}}
        path=self.path/f'{epoch}.json';temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,allow_nan=False)+'\n');temporary.replace(path)
