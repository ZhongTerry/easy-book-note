import sys
import os
sys.path.append('d:/ztrztr/¿ª·¢/noteDB')
from spider_core import plugin_mgr
adapter = plugin_mgr.find_match('https://fanqienovel.com/reader/7266734133469123087')
print('Adapter for fanqie:', adapter)
