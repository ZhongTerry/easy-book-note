# ----------------------------------------
# - mode: python - 
# - author: helloplhm-qwq - 
# - name: variable.py - 
# - project: lx-music-api-server - 
# - license: MIT - 
# ----------------------------------------
# This file is part of the "lx-music-api-server" project.

import os as _os

debug_mode = True
log_length_limit = 50000
log_file = False
running = True
config = {}
workdir = _os.getcwd()
banList_suggest = 0
iscn = True
fake_ip = None
aioSession = None
qdes_lib_loaded = False
use_cookie_pool = False
running_ports = []
use_proxy = False
http_proxy = ''
https_proxy = ''
