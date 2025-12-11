#!/usr/bin/env python3

# TODO: update docs re logging

import sys, os, time, functools, tomllib, json

TIME_AT_SCRIPT_LAUNCH = time.time()

INFINITE = 0xFFFFFFFF
WAIT_FAILED = 0xFFFFFFFF
WAIT_TIMEOUT = 0x00000102

DEFAULT_CONFIG = {
  'create_snapshots': True,
  'log_level': 'auto',
  'log_poll_delay': 1.5,
  'log_tail_lines': 10,
  'min_time_between_syncs': 0,
  'max_time_between_syncs': 24 * 60 * 60,
  'no_check_updated': False,
  'wait_until_stable_before_sync': False,
  'initial_stable_wait_delay': 60,
  'max_stable_wait_delay': 5 * 60,
  'stable_wait_backoff_rate': 1.2,
  'stable_wait_iterations': 10,
  'log_stable_wait': True,
  'lock_in_state_dir': False,
  'display_notification_after_auto_sync': True,
  'display_notification_after_manual_sync': True,
  'launch_background_notifer_after_mark': True,
  'play_alert_tone_with_notification': False,
  'alert_tone':
    'sine=f=300:d=0.5[0];sine=f=500:d=0.5[1];[0][1]concat=n=2:v=0:a=1',
  'play_alert_sound_with_notification': True,
  'alert_sound': '/usr/share/sounds/Oxygen-Im-Nudge.ogg',
  'show_alert_sound_errors': False,
  'snapshots_to_keep': 20,
  'disk_usage_path': '/',
  'buffer_size': 1024**2,
  'syncable_paths': {},
}

LOG_LEVELS = {i:idx for idx, i in
              enumerate('none,error,silent,auto,all'.split(','))}

KNOWN_MODIFY_WINDOWS = {
  'onedrive': '1s',
}

_locks = {}

def acquire_lock(suffix = '', blocking = True):
  try:
    import fcntl, errno
    if get_config()['lock_in_state_dir']:
      d = os.path.dirname(get_config()['state_path'])
      f = f'lock{suffix}'
    else:
      import tempfile
      d = tempfile.gettempdir()
      f = f'prbsync{suffix}.{os.getuid()}.lock'
    while True:
      try:
        fh = open(os.path.join(d, f), 'wb')
      except FileNotFoundError:
        if get_config()['lock_in_state_dir']:
          os.makedirs(d, exist_ok = True)
          fh = open(os.path.join(d, f), 'wb')
        else:
          raise
      try:
        fcntl.lockf(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
      except OSError as ex:
        if ex.errno in (errno.EACCES, errno.EAGAIN) and not blocking:
          return False
        raise ex
      try:
        if os.fstat(fh.fileno()) == os.stat(fh.name):
          break
      except FileNotFoundError:
        pass
      fh.close()
    _locks['fh'+suffix] = fh
  except ModuleNotFoundError:
    import ctypes
    n = 'prbsync' + suffix + '-mutex'
    handle = ctypes.windll.kernel32.CreateMutexW(0, False, n.encode())
    if not handle:
      raise ctypes.WinError()
    r = ctypes.windll.kernel32.WaitForSingleObject(handle, INFINITE if blocking else 0)
    if r == WAIT_FAILED:
      raise ctypes.WinError()
    if r == WAIT_TIMEOUT:
      return False
    _locks['handle'+suffix] = handle
  return True

def release_lock(suffix=''):
  try:
    fh = _locks.pop('fh' + suffix)
    if not get_config()['lock_in_state_dir']:
      os.remove(fh.name)
    fh.close()
  except KeyError:
    import ctypes
    try:
      handle = _locks.pop('handle' + suffix)
      if not ctypes.windll.kernel32.ReleaseMutex(handle):
        raise ctypes.WinError()
      if not ctypes.windll.kernel32.CloseHandle(handle):
        raise ctypes.WinError()
    except KeyError:
      return False
  return True

def get_config_path():
  prbsync_config = os.environ.get('PRBSYNC_CONFIG')
  if prbsync_config:
    return prbsync_config
  xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
  if xdg_config_home:
    return os.path.join(xdg_config_home, 'prbsync.toml')
  appdata = os.environ.get('APPDATA')
  if appdata:
    return os.path.join(appdata, 'prbsync.toml')
  return os.path.expanduser(os.path.join('~', '.config', 'prbsync.toml'))

@functools.cache
def get_config():
  try:
    with open(get_config_path(), 'rb') as f:
      user_config = tomllib.load(f)
  except FileNotFoundError:
    user_config = {}
  config = json.loads(json.dumps(DEFAULT_CONFIG))
  config.update(user_config)

  state_path = config.get('state_path')
  log_path = config.get('log_path')
  if not state_path or not log_path or not snapshot_dir:
    state_home = os.environ.get('XDG_STATE_HOME')
    if not state_home:
      state_home = os.environ.get('LOCALAPPDATA')
    if not state_home:
      state_home = os.path.expanduser(os.path.join('~', '.local', 'state'))
    if not state_path:
      config['state_path'] = os.path.join(state_home, 'prbsync', 'state.json')
      config['log_path'] = os.path.join(state_home, 'prbsync', 'log.txt')
  snapshot_path = config.get('snapshot_path')
  if not snapshot_path:
    data_home = os.environ.get('XDG_DATA_HOME')
    if not data_home:
      data_home = os.path.expanduser(os.path.join('~', '.local', 'share'))
    config['snapshot_path'] = os.path.join(data_home, 'snapshots')
  return config

def read_state():
  state = {}
  try:
    with open(get_config()['state_path'], 'r') as f:
      js = json.load(f)
    if (due := js.get('sync_due')) is not None:
      state['sync_due'] = due
    for name in get_config()['syncable_paths'].keys():
      for k in ('_last_auto_sync_start', '_last_auto_sync_end',
                '_last_manual_sync_start', '_last_manual_sync_end'):
        if (v := js.get(k := name + k)) is not None:
          state[k] = v
    return state
  except (json.JSONDecodeError, AttributeError):
    return {'sync_due': True}
  except FileNotFoundError:
    return state

def write_state(state):
  for _ in range(2):
    try:
      with open(get_config()['state_path'], 'w') as f:
        json.dump(state, f)
        return
    except FileNotFoundError:
      os.mkdir(os.path.dirname(get_config()['state_path']))

@functools.cache
def get_translation_manifest():
  try:
    with open(get_config()['translation_file'], 'r') as f:
      return json.load(f)
  except (KeyError, FileNotFoundError, json.JSONDecodeError):
    pass
  candidates = (
    (get_config().get('translation_dir'), '!', 'prbsync.json'),
    (get_config().get('translation_dir'), 'prbsync.!.json'),
    ('/', 'usr','share','locale', '!', 'LC_MESSAGES', 'prbsync.json'),
    ('/', 'usr','share','locale','!', 'prbsync.json'),
    ('/', 'usr','share','locale', 'prbsync.!.json'),
    (os.path.dirname(__file__), 'locale', '!', 'prbsync.json'),
    (os.path.dirname(__file__), 'prbsync.!.json'),
    (os.path.dirname(__file__), '!', 'prbsync.json'),
    ('locale', '!', 'prbsync.json'),
    ('prbsync.!.json',),
    ('!', 'prbsync.json'),
  )
  lang, _ = __import__('locale').getlocale()
  for candidate in candidates:
    try:
      with open(os.path.join(*candidate).replace('!', lang), 'r') as f:
        return json.load(f)
    except (TypeError, FileNotFoundError, NotADirectoryError,
            json.JSONDecodeError, PermissionError):
      pass
  return {}

def TR(message):
  return get_translation_manifest().get(message) or message

@functools.cache
def get_locale_config():
  return {k:v for k, v in
          map(lambda i: i.split(':'),
              TR('LocaleConfig:,text_direction:ltr').split(','))}

def log(level, *msg, flush = False):
  if level != 'silent':
    sys.stdout.write(' '.join(map(str, msg)) + '\n')
    sys.stdout.flush()
  level_number = LOG_LEVELS[level]
  desired = LOG_LEVELS.get(get_config().get('log_level').lower())
  if desired is None:
    desired = DEFAULT_CONFIG['log_level']
  if level_number <= desired:
    ESC = chr(27)
    in_escape_sequence = False
    buf = ''
    sequence_buf = ''
    for c in ' '.join(map(str.rstrip, map(str, msg))):
      if not in_escape_sequence and c == ESC:
        in_escape_sequence = True
      elif in_escape_sequence and c == 'm':
        in_escape_sequence = False
        sequence_buf = ''
      elif not in_escape_sequence:
        buf += c
      if in_escape_sequence:
        sequence_buf += c
    if sequence_buf:
      buf += sequence_buf
    if (_log_fh := globals().get('_log_fh')) is None:
      if os.name == 'nt':
        acquire_lock(suffix = '-log')
      os.makedirs(os.path.dirname(get_config()['log_path']), exist_ok = True)
      _log_fh = globals()['_log_fh'] = open(get_config()['log_path'], 'a')
      __import__('atexit').register(_log_fh.close)
    _log_fh.write(time.strftime(TR('[{0} %c PID:{1}] {2}\n')).format(level, os.getpid(), buf))
    if flush:
      _log_fh.flush()
      if os.name == 'nt':
        globals().pop('_log_fh', None)
        release_lock(suffix = '-log')

def acquire_lock_with_log_tailing(loglevel, force_trailing_blank_line = False):
  if loglevel == 'silent':
    return acquire_lock()
  if (lines := get_config()['log_tail_lines']) < 1:
    return acquire_lock()
  start = 0
  printed_log_line = False
  while not acquire_lock(blocking = False):
    buf = ''
    if os.name == 'nt':
      acquire_lock(suffix = '-log')
    with open(get_config()['log_path'], 'r') as f:
      end = f.seek(0, 2)
      offset = end
      while offset > start and buf.count('\n') < (lines - 1):
        run = min(get_config()['buffer_size'], offset - start)
        offset -= run
        f.seek(offset)
        buf = (f.read(run) or '') + buf
    if os.name == 'nt':
      release_lock(suffix = '-log')
    start = end
    for line in (buf_lines := buf.splitlines()[-lines:]):
      sys.stdout.write(line + '\n')
    if len(buf_lines) > 0:
      printed_log_line = True
      sys.stdout.flush()
    time.sleep(get_config()['log_poll_delay'])
  if printed_log_line or force_trailing_blank_line:
    print('')
  return True

def handle_exception(exc_type, exc_value, exc_tb):
  if issubclass(exc_type, KeyboardInterrupt):
    return sys.__excepthook__(exc_type, exc_value, exc_tb)
  import traceback
  tb = '\n'.join(traceback.format_exception(exc_type, exc_value, exc_tb))
  for line in filter(len, tb.splitlines()):
    log('error', line)

def run_with_log(level,
                 args,
                 capture_output = False,
                 check = True,
                 shell = False):
  import subprocess
  proc = subprocess.Popen(args,
                          stdout = subprocess.PIPE,
                          stderr = subprocess.STDOUT,
                          shell = shell)
  buf = b''
  while True:
    c = proc.stdout.read(1)
    if not c and proc.poll() is not None:
      break
    if c == b'\n':
      log(level, buf.decode())
      buf = b''
    else:
      buf += c
  if check and proc.poll() != 0:
    raise subprocess.CalledProcessError(proc.returncode, args)
  return proc

def run_detached(args):
  import subprocess
  cmd = [sys.executable, __file__] + args
  kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
  if detached_process := getattr(subprocess, 'DETACHED_PROCESS', None):
    kwargs['creationflags'] = detached_process
  else:
    kwargs['start_new_session'] = True
  log('silent', TR('Launch detached process: {0}').format(cmd))
  subprocess.Popen(cmd, **kwargs)

def make_filter_args(path):
  args = []
  filt = path.get('auto_sync_filter')
  if type(filt) is list:
    for f in filt:
      args.extend(['--filter', f])
  elif type(filt) is str:
    args.extend(['--filter', filt])
  return args

def get_rclone_config():
  import shutil
  rclone = shutil.which('rclone')
  candidates = (
    os.path.join(os.path.dirname(rclone), 'rclone.conf'),
    '%APPDATA%/rclone/rclone.conf',
    '$XDG_CONFIG_HOME/rclone/rclone.conf',
    '~/.config/rclone/rclone.conf',
    '~/.rclone.conf',
  )
  for candidate in candidates:
    c = os.path.expandvars(os.path.expanduser(candidate))
    try:
      with open(c, 'r') as f:
        return f.read().replace('\r\n', '\n').replace('\r', '\n')
    except FileNotFoundError:
      pass
  raise FileNotFoundError(TR('rclone.conf not found'))

@functools.cache
def get_remote_type(remote):
  cfg = get_rclone_config()
  i = 0 if cfg.startswith('[{0}]'.format(remote)) else \
        cfg.index('\n[{0}]'.format(remote))
  j = cfg.find('\n[', i+1)
  rcfg = cfg[i:j] if j > 0 else cfg[i:]
  i = rcfg.index('\ntype = ') + 8
  j = rcfg.find('\n', i)
  return (rcfg[i:j] if j > 0 else rcfg[i:]).strip()

def get_modify_window_for_remote(remote):
  return KNOWN_MODIFY_WINDOWS.get(get_remote_type(remote))

def is_hydrated_and_dir_exists(sync_path):
  lp = os.path.expanduser(os.path.expandvars(sync_path['local_path']))
  if not os.path.isdir(lp):
    return False, False
  _, candidates = make_listing_path_candidates(sync_path, True)
  for candidate in candidates:
    if candidate is None:
      return False, True
    c = os.path.expanduser(os.path.expandvars(os.path.join(*candidate)))
    if os.path.isfile(c):
      return True, True

def is_hydrated(sync_path):
  return is_hydrated_and_dir_exists(sync_path)[0]

@functools.cache
def is_hydrated_by_name(name):
  return is_hydrated(get_config()['syncable_paths'][name])

def apply_default_filters(sync_path, cmd):
  remote_name, _, remote_path = sync_path['remote_path'].partition(':')
  _type = get_remote_type(remote_name)
  if _type != 'onedrive':
    return
  if remote_path not in ('/', ''):
    return
  _filter = TR('- /Personal Vault/**')
  if _filter in cmd:
    return
  cmd.extend(['--filter', _filter])

def run_rclone_with_log(loglevel, args, sync_path):
  cmd = ['rclone'] + args
  remote_name = sync_path['remote_path'].split(':')[0]
  modify_window = get_modify_window_for_remote(remote_name)
  if modify_window is not None:
    cmd += ['--modify-window', modify_window]
  if loglevel == 'all':
    cmd.append('--verbose')
  missing = object()
  retries = get_config().get('retries', missing)
  if retries is missing:
    retries = get_config()['stable_wait_iterations']
  if retries is not None:
    cmd += ['--retries', str(retries)]
  retries_sleep = get_config().get('retries_sleep', missing)
  if retries_sleep is missing:
    retries_sleep = get_config()['initial_stable_wait_delay']
  if retries_sleep is not None:
    cmd += ['--retries-sleep', str(round(float(retries_sleep)*1000))+'ms']
  if sync_path.get('no_check_updated', get_config().get('no_check_updated')):
    cmd.append('--local-no-check-updated')
  if (
    sync_path.get('apply_default_filters',
                   get_config().get('apply_default_filters',True)) and
    args[0] != 'copyto'
  ):
    apply_default_filters(sync_path, cmd)
  return run_with_log(loglevel, cmd)

def hydrate(name, path, skip_local_dir_creation = False):
  local_path = os.path.expanduser(path['local_path'])
  remote_path = os.path.expanduser(path['remote_path'])
  log('all', TR('Hydrating {0}').format(local_path))
  if skip_local_dir_creation:
    if not os.path.isdir(local_path):
      log('all', TR('{0} - path is not a directory').format(local_path))
      return False
    resync_args = ['--resync']
    take_snapshot(name, path)
  else:
    if is_hydrated_by_name(name):
      log('all', TR('{0} - path is already hydrated').format(local_path))
      return False
    subvolume = path.get('subvolume', True)
    if subvolume and get_config()['create_snapshots']:
      import shutil
      btrfs = shutil.which('btrfs')
    else:
      btrfs = None
    os.makedirs(os.path.dirname(local_path), exist_ok = True)
    if btrfs:
      run_with_log('all', (btrfs, 'subvolume', 'create', local_path))
    else:
      os.mkdir(local_path)
    resync_args = ['--resync-mode', 'path2']
  args = ['bisync', *resync_args, local_path, remote_path]
  run_rclone_with_log('all', args, path)
  is_hydrated_by_name.cache_clear()
  return True

def do_hydrate(target, skip_local_dir_creation = False):
  acquire_lock()
  path = get_config()['syncable_paths'].get(target)
  if not path:
    log('all', TR('No syncable path named {0}').format(target))
    sys.exit(1)
  r = hydrate(target, path, skip_local_dir_creation = skip_local_dir_creation)
  sys.exit(0 if r else 1)

def dehydrate(sync_path):
  local_path = os.path.expanduser(sync_path['local_path'])
  remote_path = os.path.expanduser(sync_path['remote_path'])
  log('all', TR('Dehydrating {0}').format(local_path))
  if not is_hydrated(sync_path):
    log('all', TR('{0} - path is not hydrated').format(local_path))
    return False
  run_rclone_with_log('all', ['bisync', local_path, remote_path], sync_path)
  listing_paths = []
  for local in (True, False):
    _, candidates = make_listing_path_candidates(sync_path, local)
    for candidate in candidates:
      if not candidate:
        continue
      c = os.path.expanduser(os.path.expandvars(os.path.join(*candidate)))
      if os.path.isfile(c):
        listing_paths.append(c)
        old_c = c + '-old'
        if os.path.isfile(old_c):
          listing_paths.append(old_c)
        break
  if len(listing_paths) not in (2, 4):
    log('all', TR('{0} - could not find listings while dehydrating')
                  .format(local_path))
    return False
  for path in listing_paths:
    os.remove(path)
  import shutil
  shutil.rmtree(local_path)
  is_hydrated_by_name.cache_clear()
  return True

def do_dehydrate(target):
  acquire_lock()
  sync_path = get_config()['syncable_paths'].get(target)
  if not sync_path:
    log('all', TR('No syncable path named {0}').format(target))
    sys.exit(1)
  sys.exit(0 if dehydrate(sync_path) else 1)

def take_snapshot(name, path, level = 'all'):
  if not get_config()['create_snapshots']:
    log(level, TR('Skipping snapshot because create_snapshots was false'))
    return
  subvolume = path.get('subvolume', True)
  if not subvolume:
    log(level, TR('Skipping snapshot because subvolume was false'))
    return
  import shutil
  btrfs = shutil.which('btrfs')
  if not btrfs:
    log(level, TR('Skipping snapshot because btrfs-progs is not installed'))
    return
  snapshot_name = name + '@' + str(round(time.time()))
  snapshot_path = get_config().get('snapshot_path')
  snapshot_dir = os.path.join(snapshot_path, snapshot_name)
  if not os.path.exists(snapshot_path):
    os.mkdir(snapshot_path)
  run_with_log(level, (btrfs, 'subvolume', 'snapshot', '-r',
                os.path.expanduser(path['local_path']), snapshot_dir))

def manual_sync_path(name, path, silent, state):
  local_path = os.path.expanduser(path['local_path'])
  remote_path = os.path.expanduser(path['remote_path'])

  pre_cmds = path.get('pre_sync_cmds', [])
  pre_cmds += path.get('pre_manual_sync_cmds', [])
  for cmd in pre_cmds:
    log(
      'all',
      TR('Running pre-sync command: {0}').format(cmd.strip()),
      flush = True,
    )
    run_with_log('all', cmd, shell = True)

  take_snapshot(name, path)

  state[name + '_last_manual_sync_start'] = time.time()
  run_rclone_with_log('all', ['bisync', local_path, remote_path], path)
  state[name + '_last_manual_sync_end'] = time.time()

  post_cmds = path.get('post_sync_cmds', [])
  post_cmds += path.get('post_manual_sync_cmds', [])
  for cmd in post_cmds:
    log(
      'all',
      TR('Running post-sync command: {0}').format(cmd.strip()),
      flush = True,
    )
    run_with_log('all', cmd, shell = True)

def manual_sync_paths():
  acquire_lock_with_log_tailing('all')
  log('all', TR('Starting a manual sync for all paths'))
  state = read_state()
  for name, path in get_config()['syncable_paths'].items():
    log(
      'all',
      TR('Start of manual sync for {0}').format(name),
      flush = True,
    )
    if not is_hydrated(path):
      log('all', TR('Skipping {0} - the path has not been hydrated').format(name))
      continue
    manual_sync_path(name, path, False, state)
  try:
    state.pop('sync_due')
  except KeyError:
    pass
  write_state(state)
  message = TR('Manual sync complete for all paths!')
  if get_config()['display_notification_after_manual_sync']:
    notify_with_message(message)
  else:
    log('all', message)
  release_lock()

def get_sync_paths_for_path(path):
  paths = {}
  for name, sync_path in get_config()['syncable_paths'].items():
    local = os.path.expanduser(sync_path['local_path'])
    if local.endswith(os.path.sep) or local.endswith(os.path.altsep or os.path.sep):
      local = local[:-len(os.path.sep)]
    if path == local or path.startswith(local + os.path.sep):
      paths[name] = sync_path
  return paths

def make_cache_safe_path(path):
  import re
  path = os.path.expanduser(path)
  if path.startswith(os.path.sep):
    path = path[len(os.path.sep):]
  return re.sub(r'[^a-zA-Z0-9\.]+', '_', path)

def make_listing_path_candidates(sync_path, local):
  no_check_updated = sync_path.get('no_check_updated',
                                   get_config().get('no_check_updated'))
  name = (
    ('local__' if no_check_updated else '') +
    make_cache_safe_path(sync_path['local_path']) +
    '..' +
    make_cache_safe_path(sync_path['remote_path']) +
    ('.path1.lst' if local else '.path2.lst')
  )
  candidates = (
    ('~', '.cache', 'rclone', 'bisync', name),
    ('%LOCALAPPDATA%', 'bisync', name),
    ('~', 'Library', 'Caches', 'rclone', 'bisync', name),
    None
  )
  return name, candidates

def load_listings(sync_path):
  listings = {'local_dir': os.path.expanduser(sync_path['local_path'])}
  for local in (True, False):
    name, candidates = make_listing_path_candidates(sync_path, local)
    for candidate in candidates:
      if not candidate:
        raise FileNotFoundError('Unable to find listing', name)
      try:
        with open(os.path.expanduser(os.path.expandvars(os.path.join(*candidate))), 'r') as f:
          listings['local' if local else 'remote'] = f.read()
          listings['local_path' if local else 'remote_path'] = f.name
          break
      except (FileNotFoundError, PermissionError):
        pass
  return listings

def update_listings(listings, path, size, remote_path):
  import re, datetime
  # NB: lsjson does not always return ModTime strings with the precision
  #     bisync expects so they're reconstructed here instead of using
  #     the existing string from lsjson
  st = os.stat(os.path.join(listings['local_dir'], path))
  dt = datetime.datetime.fromtimestamp(st.st_mtime)
  dt = dt.astimezone(datetime.timezone.utc)
  dt = dt.isoformat().split('.')[0].split('+')[0]
  ns = fallback_ns = str(st.st_mtime_ns % 1000000000).rjust(9, '0')
  if get_modify_window_for_remote(remote_path.split(':')[0]) == '1s':
    fallback_ns = 9 * '0'
  new_line_pre = '- ' + str(size).rjust(8) + ' - - '
  new_line_post = '+0000 ' + json.dumps(path)
  listings['modified'] = True
  for place in ('local', 'remote'):
    listing_updated = False
    timestamp = dt + '.' + ns
    new_line = new_line_pre + timestamp + new_line_post
    for line in listings[place].splitlines():
      if line[0] == '#':
        continue
      match = re.match(
        r'-\s+(\d+)\s+-\s+-\s+([0-9\-A-Z\:\+\.]+)\s+(".+?"$)', line
      )
      if not datetime.datetime.fromisoformat(match.group(2)).microsecond:
        timestamp = dt + '.' + fallback_ns
        new_line = new_line_pre + timestamp + new_line_post
      p = json.loads(match.group(3))
      if p == path:
        if not listing_updated:
          listings[place] = listings[place].replace(line, new_line)
          listing_updated = True
        else:
          raise Exception('Unexpected duplicate local listing', path)
    if not listing_updated:
      if not listings[place].endswith('\n'):
        listings[place] += '\n'
      listings[place] += new_line + '\n'
  return timestamp

def write_listings(listings):
  if not listings.get('modified'):
    return
  with open(listings['local_path'], 'w') as f:
    f.write(listings['local'])
  with open(listings['remote_path'], 'w') as f:
    f.write(listings['remote'])

def auto_sync_path(name, path, always_check_remote, silent, state):
  import datetime, math
  changed = False
  manual_sync_due = False
  paths_due_for_auto_sync = {}
  lpath = os.path.expanduser(path['local_path'])
  lpath += '' if lpath.endswith(os.path.sep) else os.path.sep
  loglevel = 'silent' if silent else 'auto'
  start = time.time()
  last_auto_sync = state.get(name + '_last_auto_sync_end') or 0
  last_manual_sync = state.get(name + '_last_manual_sync_end') or 0
  if last_manual_sync > TIME_AT_SCRIPT_LAUNCH:
    log(loglevel, TR('Skipping {0} - a manual sync already occurred').format(name))
    return changed, manual_sync_due, list_files_matching_auto_sync_filter(path)
  if last_auto_sync > TIME_AT_SCRIPT_LAUNCH:
    log(loglevel, TR('Skipping {0} - an auto sync already occurred').format(name))
    return changed, manual_sync_due, list_files_matching_auto_sync_filter(path)
  if not is_hydrated_by_name(name):
    log(loglevel, TR('Skipping {0} - the path has not been hydrated').format(name))
    return changed, manual_sync_due, list_files_matching_auto_sync_filter(path)
  min_time = path.get('min_time_between_syncs',
                      get_config()['min_time_between_syncs'])
  if (delta := min_time - (start - max(last_auto_sync, last_manual_sync))) > 0:
    log(loglevel, TR('Last sync for {0} was too recent').format(name))
    log(loglevel, TR('  waiting for {0}').format(human_duration(delta)))
    log(
      loglevel,
      TR('  sync will resume at {0}').format(human_time(start + delta, fmt = '%c')),
      flush = True,
    )
    time.sleep(delta)
  local_files = list_files_matching_auto_sync_filter(path)
  wait_until_stable_before_sync = path.get('wait_until_stable_before_sync')
  if wait_until_stable_before_sync is None:
    wait_until_stable_before_sync = \
      get_config()['wait_until_stable_before_sync']
  delay = initial_delay = get_config()['initial_stable_wait_delay']
  while wait_until_stable_before_sync:
    log(loglevel, TR('Waiting until stable before syncing'))
    log(loglevel, TR('  current delay: {0} ({1})')
                     .format(delay, human_duration(delay)),
        flush = True)
    time.sleep(delay)
    lf = list_files_matching_auto_sync_filter(path)
    if lf == local_files:
      break
    delay = min(
      delay * get_config()['stable_wait_backoff_rate'],
      get_config()['max_stable_wait_delay']
    )
    local_files = lf
  listings = load_listings(path)
  pre_cmds = path.get('pre_sync_cmds', [])
  pre_cmds += path.get('pre_auto_sync_cmds', [])
  for cmd in pre_cmds:
    log(loglevel, TR('Running pre-sync command: {0}').format(cmd.strip()))
    run_with_log(loglevel, cmd, shell = True)
  for fpath, f in local_files.items():
    mtime = datetime.datetime.fromisoformat(f['ModTime']).timestamp()
    if mtime > last_manual_sync and mtime > last_auto_sync:
      paths_due_for_auto_sync[os.path.join(lpath, fpath)] = f
  if len(paths_due_for_auto_sync) > 0 or always_check_remote:
    log(
      loglevel,
      TR('Listing remote files for {0}').format(name),
      flush = True,
    )
    remote_files = list_files_matching_auto_sync_filter(path, remote = True)
    for p, f in paths_due_for_auto_sync.items():
      exisiting_mtimes = list(
        map(lambda i: round(datetime.datetime.fromisoformat(i['ModTime']).timestamp()),
          filter(lambda i: i['Path'] == f['Path'], remote_files.values())))
      last_local_sync = max(last_manual_sync, last_auto_sync)
      last_remote_mtime = max(exisiting_mtimes, default=-1)
      if last_remote_mtime > last_local_sync:
          log(loglevel, TR('remote and local modification times have changed for {}').format(p))
          log(loglevel, TR('marking a manual sync as due'))
          manual_sync_due = True
          continue
      if not changed:
        take_snapshot(name, path, loglevel)
      r = path['remote_path']
      if not r.endswith('/'):
        r += '/'
      r += f['Path']
      log(loglevel, TR('Uploading {0} to {1}...').format(p, r), flush = True)
      run_rclone_with_log(loglevel, ['copyto', p, r], path)
      tstamp = update_listings(listings, f['Path'], f['Size'], r)
      run_rclone_with_log(loglevel, ['touch', r, '--timestamp', tstamp], path)
      changed = True
    for fpath, f in remote_files.items():
      loc = os.path.join(lpath, f['Path'])
      if loc not in paths_due_for_auto_sync:
        try:
          lmtime = math.floor(os.path.getmtime(loc))
        except FileNotFoundError:
          lmtime = 0
        rmtime = math.floor(datetime.datetime.fromisoformat(f['ModTime']).timestamp())
        if lmtime == rmtime:
          continue
        if lmtime > rmtime:
          if not path.get('no_check_updated', get_config().get('no_check_updated')):
            log(loglevel,
                TR('the local modification time is newer than the remote one for {}').format(f))
            log(loglevel, TR('marking a manual sync as due'))
            manual_sync_due = True
          continue
        if not changed:
          take_snapshot(name, path, loglevel)
        r = path['remote_path']
        if not r.endswith('/'):
          r += '/'
        r += fpath
        log(loglevel, TR('Downloading {0} to {1}...').format(r, loc))
        run_rclone_with_log(loglevel, ['copyto', r, loc], path)
        update_listings(listings, fpath, f['Size'], r)
        changed = True
    if changed:
      state[name+'_last_auto_sync_start'] = start
      state[name+'_last_auto_sync_end'] = time.time()
  write_listings(listings)
  post_cmds = path.get('post_sync_cmds', [])
  post_cmds += path.get('post_auto_sync_cmds', [])
  for cmd in post_cmds:
    log(loglevel, TR('Running post-sync command: {0}').format(cmd.strip()))
    run_with_log(loglevel, cmd, shell = True)
  return changed, manual_sync_due, local_files

def do_mark(paths, always_check_remote=False):
  paths = [os.path.abspath(os.path.expanduser(i)) for i in paths]
  path_to_target = {
    i: (list(filter(
         lambda i: is_hydrated_by_name(i[0]),
         get_sync_paths_for_path(i).items(),
       )) or [None])[0] for i in paths
  }
  targets = {k:v for k,v in filter(bool, path_to_target.values())}
  manual_sync_due = not paths
  if manual_sync_due or targets:
    state_modified = False
    auto_syncable_paths = set()
    acquire_lock()
    state = read_state()
    for name, spath in targets.items():
      c, m, lf = auto_sync_path(name, spath, always_check_remote, False, state)
      state_modified = state_modified or c
      manual_sync_due = manual_sync_due or m
      auto_syncable_paths.update({
        os.path.expanduser(os.path.join(spath['local_path'], i)) for i in lf
      })
    for path in paths:
      if path not in auto_syncable_paths and path_to_target.get(path):
        log('auto', TR('marked path {} is not auto-syncable').format(path))
        log('auto', TR('marking a manual sync as due'))
        manual_sync_due = True
    old_sync_due = state.get('sync_due')
    if state_modified or (manual_sync_due and not old_sync_due):
      state['sync_due'] = old_sync_due or manual_sync_due
      write_state(state)
    else:
      os.utime(get_config()['state_path'])
    if get_config()['launch_background_notifer_after_mark']:
      run_detached(['wait_and_notify'])
    else:
      release_lock()

def compute_sync_due(state):
  if state.get('sync_due'):
    return True
  max_time = get_config()['max_time_between_syncs']
  for name, path in get_config()['syncable_paths'].items():
    if not is_hydrated(path):
      continue
    last = state.get(name + '_last_manual_sync_start')
    if not last or (TIME_AT_SCRIPT_LAUNCH - last) > max_time:
      return True
  return False

def find_keyboard_shortcut():
  shortcut = get_config().get('sync_keyboard_shortcut')
  if shortcut:
    return shortcut
  if os.environ.get('XDG_SESSION_DESKTOP') == 'KDE':
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME') or '~/.config'
    xdg_data_home = os.environ.get('XDG_DATA_HOME') or '~/.local/share'
    name = None
    try:
      with open(os.path.expanduser(os.path.join(xdg_config_home, 'kglobalshortcutsrc')), 'r') as f:
        for line in map(str.strip, f):
          if line.startswith('[services][') and line[-1] == ']':
            name = line[11:-1]
          elif line and line[0] == '[':
            name = None
          elif line.startswith('_launch=') and name:
            shortcut = line[8:]
            if not shortcut:
              continue
            try:
              with open(os.path.expanduser(os.path.join(xdg_data_home, 'applications', name)), 'r') as f:
                if len(tuple(filter(lambda i: i.startswith('Exec=') and 'prbsync sync' in i, f.readlines()))) == 1:
                  return shortcut.replace('+', ' + ')
            except FileNotFoundError:
              pass
    except FileNotFoundError:
      pass
  return None

def notify_with_message(message):
  import shutil, subprocess
  appname = TR('PRBSync')
  fmt = TR('{0}: {1}').format(appname, message)
  log('all', fmt)
  if notify_send := shutil.which('notify-send'):
    subprocess.run((notify_send, '-a', appname, message),
                   capture_output = True)
  if termux_toast := shutil.which('termux-toast'):
    subprocess.run((termux_toast, fmt), capture_output = True)
  if os.name == 'nt' and (powershell := shutil.which('powershell')):
    powershell_code = (
      '$t=[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.' +
      'Notifications,ContentType=WindowsRuntime]::GetTemplateContent(' +
      '[Windows.UI.Notifications.ToastTemplateType,Windows.UI.Notifications,' +
      'ContentType=WindowsRuntime]::ToastText02);$t.SelectSingleNode(\'' +
      '//text[@id="1"]\').InnerText=TITLE;$t.SelectSingleNode(\''+
      '//text[@id="2"]\').InnerText=BODY;[Windows.UI.Notifications.' +
      'ToastNotificationManager]::CreateToastNotifier(TITLE).Show($t)'
    ).replace('TITLE', repr(appname)).replace('BODY', repr(message))
    subprocess.check_call((powershell, '-c', powershell_code))
  if (get_config()['play_alert_tone_with_notification'] or
      get_config()['play_alert_sound_with_notification']):
    ffplay = shutil.which('ffplay')
    if get_config()['show_alert_sound_errors']:
      loglevel, stderr = 'warning', None
    else:
      loglevel, stderr = 'quiet', subprocess.DEVNULL
    if ffplay and get_config()['play_alert_tone_with_notification']:
      subprocess.check_output((ffplay, '-autoexit', '-nodisp', '-f', 'lavfi',
                                '-i', get_config()['alert_tone'],
                                '-loglevel', loglevel), stderr = stderr)
    if ffplay and get_config()['play_alert_sound_with_notification']:
      alert_sound = get_config()['alert_sound']
      if os.path.exists(alert_sound):
        subprocess.check_output((ffplay, '-autoexit', '-nodisp', alert_sound,
                                  '-loglevel', 'quiet'), stderr = stderr)
  if custom_cmd := get_config().get('custom_notification_command'):
    import shlex
    subprocess.check_call(shlex.split(custom_cmd))

def do_notify(wait_until_stable):
  if wait_until_stable:
    if not acquire_lock('-notify', False):
      return
    if get_config()['log_stable_wait']:
      acquire_lock()
      log('auto', TR('wait_and_notify acquired notification lock'))
      release_lock()
    remaing = get_config()['stable_wait_iterations']
    current_delay = get_config()['initial_stable_wait_delay']
    old_mtime = None
    while remaing > 0:
      if get_config()['log_stable_wait']:
        acquire_lock()
        log('auto', TR('Waiting until stable before notifying'))
        log('auto', TR('  remaining iterations: {0}').format(remaing))
        log('auto', TR('  current delay: {0} ({1})').format(
            current_delay, human_duration(current_delay)),
            flush = True)
        release_lock()
      time.sleep(current_delay)
      try:
        mtime = os.path.getmtime(get_config()['state_path'])
      except FileNotFoundError:
        mtime = None
      if mtime == old_mtime:
        remaing -= 1
        current_delay = get_config()['initial_stable_wait_delay']
      else:
        remaing = get_config()['stable_wait_iterations']
        current_delay = min(current_delay * get_config()['stable_wait_backoff_rate'],
                            get_config()['max_stable_wait_delay'])
      old_mtime = mtime
    if get_config()['log_stable_wait']:
      acquire_lock()
      log('auto', TR('state is stable'))
      release_lock()

  acquire_lock()
  state = read_state()
  due = compute_sync_due(state)
  if due:
    shortcut = find_keyboard_shortcut()
    if shortcut:
      message = TR('A sync is due! Run \'prbsync sync\' or press {0}.').format(shortcut)
    else:
      message = TR('A sync is due! Run \'prbsync sync\'')
    notify_with_message(message)
  release_lock()
  if wait_until_stable:
    release_lock('-notify')
  sys.exit(0 if due else 1)

def human_duration(t):
  days = int(t // (24*60*60))
  hours = int((t // (60*60)) % 24)
  minutes = int((t // (60)) % 60)
  seconds = round(t % 60)
  r = []
  if days > 0:
    r.append(TR('1 day') if days == 1 else (TR('{0} days').format(days)))
  if hours > 0:
    r.append(TR('1 hour') if hours == 1 else (TR('{0} hours').format(hours)))
  if minutes > 0:
    r.append(TR('1 minute') if minutes == 1 else (TR('{0} minutes').format(minutes)))
  if seconds > 0 or len(r) < 1:
    r.append(TR('1 second') if seconds == 1 else (TR('{0} seconds').format(seconds)))
  return ' '.join(r)

def human_time(t, fmt = None):
  if t is None:
    return TR('Never')
  import datetime
  dt = datetime.datetime.fromtimestamp(t)
  duration = human_duration(TIME_AT_SCRIPT_LAUNCH - t)
  return dt.strftime(fmt or TR('%c ({0} ago)').format(duration))

def do_query():
  acquire_lock_with_log_tailing('all')
  print(TR('Config Path: {0}').format(get_config_path()))
  print(TR('State Path: {0}').format(get_config()['state_path']))
  print(TR('Log Path: {0}').format(get_config()['log_path']))
  print(TR('Snapshot Path: {0}').format(get_config()['snapshot_path']))
  if not (bg := not acquire_lock('-notify', False)):
    release_lock('-notify')
  state = read_state()
  release_lock()
  for name, path in sorted(get_config()['syncable_paths'].items()):
    print(name)
    if (h := is_hydrated_and_dir_exists(path))[0]:
      hydrated = TR('Yes')
    elif h[1]:
      hydrated = TR('No (but its local path exists)')
    else:
      hydrated = TR('No')
    print(TR('  Hydrated: {}').format(hydrated))
    last = state.get(name + '_last_auto_sync_start')
    print(TR('  Last Auto Sync: {0}').format(human_time(last)))
    if last is not None:
      d = state.get(name + '_last_auto_sync_end') - last
      print(TR('  Last Auto Sync Duration: {0}').format(human_duration(d)))
    last = state.get(name + '_last_manual_sync_start')
    print(TR('  Last Manual Sync: {0}').format(human_time(last)))
    if last is not None:
      d = state.get(name + '_last_manual_sync_end') - last
      print(TR('  Last Manual Sync Duration: {0}').format(human_duration(d)))
  print(TR('Background Notifier Running: {}').format(TR('Yes') if bg else TR('No')))
  print(TR('Max Time Between Syncs: {0}').format(human_duration(get_config()['max_time_between_syncs'])))
  due = compute_sync_due(state)
  print(TR('Sync Due: Yes') if due else TR('Sync Due: No'))
  print('')
  sys.exit(0 if due else 1)

def do_json_query(log_tailing = False):
  if log_tailing:
    acquire_lock_with_log_tailing('auto', force_trailing_blank_line = True)
  else:
    acquire_lock()
  state = read_state()
  print(json.dumps({
    'config_path': get_config_path(),
    'config': get_config(),
    'state': state,
    'sync_due': compute_sync_due(state),
    'is_hydrated': {
      k: is_hydrated(v) for k,v in get_config()['syncable_paths'].items()
    },
  },
  indent = 2,
  sort_keys = True))

def list_files_matching_auto_sync_filter(path, remote = False):
  import subprocess
  filter_args = make_filter_args(path)
  if len(filter_args) < 2:
    return {}
  root = os.path.expanduser(path['remote_path' if remote else 'local_path'])
  cmd = ['rclone', 'lsjson', '-R'] + filter_args + [root]
  js = json.loads(subprocess.check_output(cmd))
  return {f['Path']:f for f in filter(lambda i: not i['IsDir'], js)}

def do_lsf(target):
  targets = get_config()['syncable_paths']
  if target:
    t = targets.get(target)
    if not t:
      log('error', TR('{0} is not the name of a syncable path').format(target))
      return
    targets = {target:t}
  for target in targets.values():
    print(target['local_path'])
    for f in list_files_matching_auto_sync_filter(target).values():
      print(TR('  {0}').format(f))

def do_auto(always_check_remote, silent, target_path):
  manual_sync_due = False
  state_modified = False
  loglevel = 'silent' if silent else 'auto'
  acquire_lock_with_log_tailing(loglevel)
  state = read_state()
  log(loglevel, TR('Starting an auto sync (always_check_remote = {0})').format(
    TR('Yes') if always_check_remote else TR('No')))
  log(loglevel, TR('auto sync target path = {0}').format(repr(target_path)))
  for name, path in get_config()['syncable_paths'].items():
    if target_path and name != target_path:
      m = TR('Skipping {0} due to not matching the target path').format(name)
      log(loglevel, m)
      continue
    log(loglevel, TR('Synching {0}').format(name))
    c, m, _ = auto_sync_path(name, path, always_check_remote, silent, state)
    state_modified = state_modified or c
    manual_sync_due = manual_sync_due or m
  if manual_sync_due and not state.get('sync_due'):
    state['sync_due'] = True
    state_modified = True
  if state_modified:
    write_state(state)
  message = TR('Auto sync complete!')
  if get_config()['display_notification_after_auto_sync']:
    notify_with_message(message)
  else:
    log(loglevel, message)
  release_lock()

def hash_file(path):
  import hashlib
  bufsize = get_config()['buffer_size']
  h = hashlib.sha256()
  with open(path, 'rb') as f:
    while True:
      buf = f.read(bufsize)
      if not buf:
        break
      h.update(buf)
  return h.digest()

def get_sorted_prbsync_snapshots():
  import bisect
  snapshots = {k: [] for k in get_config()['syncable_paths'].keys()}
  for snapshot in os.listdir(get_config()['snapshot_path']):
    sp = snapshot.split('@')
    if len(sp) == 2 and sp[1].isdigit():
      name = sp[0]
      snaps = snapshots.get(name)
      if snaps is not None:
        bisect.insort(snaps,
                      os.path.join(get_config()['snapshot_path'], snapshot),
                      key = lambda i: int(i.split('@')[-1]))
  return snapshots

def diff_snapshot_to_current_or_snapshot(snapshot_a, snapshot_b = None, print_progress = True):
  import stat
  spa = os.path.basename(snapshot_a).split('@')
  if len(spa) != 2:
    raise ValueError('Not a valid snapshot', snapshot_a)
  current_path = get_config()['syncable_paths'].get(spa[0])
  if not current_path:
    raise ValueError('Not a syncable path snapshot', snapshot_a)
  if snapshot_b is None:
    snapshot_b = os.path.expanduser(current_path['local_path'])
  else:
    spb = os.path.basename(snapshot_b).split('@')
    if len(spb) != 2:
      raise ValueError('Not a valid snapshot', snapshot_b)
    if spa[0] != spb[0]:
      raise ValueError('Snapshot subvolumes do not match', snapshot_a, snapshot_b)
  snapshot_a_paths = set()
  if not snapshot_a.endswith(os.path.sep):
    snapshot_a += os.path.sep
  for root, dirs, files in os.walk(snapshot_a):
    eroot = root[len(snapshot_a):]
    for i in (dirs + files):
      snapshot_a_paths.add(os.path.join(eroot, i))
  snapshot_b_paths = set()
  if not snapshot_b.endswith(os.path.sep):
    snapshot_b += os.path.sep
  for root, dirs, files in os.walk(snapshot_b):
    eroot = root[len(snapshot_b):]
    for i in (dirs + files):
      snapshot_b_paths.add(os.path.join(eroot, i))
  created_paths = snapshot_b_paths - snapshot_a_paths
  deleted_paths = snapshot_a_paths - snapshot_b_paths
  changed_paths = set()
  WIDTH = 80
  common_paths = snapshot_a_paths.intersection(snapshot_b_paths)
  for idx, path in enumerate(common_paths):
    apath = os.path.join(snapshot_a, path)
    bpath = os.path.join(snapshot_b, path)
    ast = os.stat(apath)
    bst = os.stat(bpath)
    if (ast.st_mode != bst.st_mode or
        ast.st_mtime != bst.st_mtime or
        ast.st_size != bst.st_size):
      changed_paths.add(path)
    elif stat.S_ISREG(ast.st_mode):
      if print_progress:
        st = TR('Computing hashes [{0}/{1}]').format(idx + 1, len(common_paths))[:WIDTH]
        sys.stdout.write('\r' + st + (' ' * (WIDTH-len(st))))
        sys.stdout.flush()
      if hash_file(apath) != hash_file(bpath):
        changed_paths.add(path)
  if print_progress:
      sys.stdout.write('\r' + (' ' * WIDTH) + '\r')
      sys.stdout.flush()
  return created_paths, deleted_paths, changed_paths

def print_snapshot_diff(snapshot_a, snapshot_b = None):
  r = diff_snapshot_to_current_or_snapshot(snapshot_a, snapshot_b)
  for i, name in enumerate((TR('Created'), TR('Deleted'), TR('Changed'))):
    print(TR(' - {0} - ').format(name))
    if len(r[i]) > 0:
      for path in sorted(r[i]):
        print('    ' + path)
    else:
      print(TR('  * None * '))
    print('')
  return r

def do_diff(args):
  if len(args) not in (1, 2):
    log('error', TR('Invalid args for diff: {0}').format(args))
    return
  snapshot_a = args[0]
  snapshot_b = args[1] if len(args) > 1 else None
  print_snapshot_diff(snapshot_a, snapshot_b)

def delete_snapshot(snapshot):
  print(TR('Deleting {0}...').format(snapshot))
  args = ['btrfs', 'subvolume', 'delete', snapshot]
  if hasattr(os, 'getuid') and os.getuid() != 0:
    args = ['sudo'] + args
  run_with_log('all', args)

def do_clean(incremental, targets):
  if len(targets) < 1:
    subvolumes = set(get_config()['syncable_paths'].keys())
  else:
    subvolumes = set()
    for target in targets:
      if target not in get_config()['syncable_paths']:
        print(TR('{0} is not the name of a syncable path').format(target))
        return
      subvolumes.add(target)

  import shutil
  usage = shutil.disk_usage(get_config()['disk_usage_path'])
  for line in TR('Disk Usage Before Cleanup:\n  {0}\n').format(usage).splitlines():
    log('all', line)

  all_ssnaps = get_sorted_prbsync_snapshots()
  for subvolume in subvolumes:
    ssnaps = all_ssnaps[subvolume]
    print(TR('Found 1 snapshot for {0}').format(subvolume) if len(ssnaps) == 1 else
          TR('Found {0} snapshots for {1}').format(len(ssnaps), subvolume))
    stop_idx = len(ssnaps) - get_config()['snapshots_to_keep']
    current_idx = 0
    while current_idx < stop_idx:
      snapshot_a = ssnaps[current_idx]
      if incremental:
        snapshot_b = ssnaps[current_idx+1] if current_idx < len(ssnaps) else None
      else:
        snaps = ssnaps[:stop_idx]
        snapshot_b = ssnaps[stop_idx] if len(ssnaps) > get_config()['snapshots_to_keep'] else None
      print('')
      if incremental:
        print(TR('Diffing the oldest two snapshots:') if current_idx < 1 else
              TR('Diffing the next oldest two snapshots:'))
      else:
        print(TR('Diffing the oldest snapshot') if len(snaps) == 1 else
              TR('Diffing the oldest {0} snapshots').format(len(snaps)))
      print('  ' + snapshot_a)
      print('  ' + (TR('(Current Version)') if snapshot_b is None else snapshot_b))
      print('')
      print_snapshot_diff(snapshot_a, snapshot_b)
      print('')
      if incremental or len(snaps) == 1:
        print(TR('Delete oldest snapshot? {0}').format(snapshot_a))
      else:
        print(TR('Delete the oldest {0} snapshots? {1} to {2}').format(len(snaps), snaps[0], snaps[-1]))
      inp = input(TR('(y/n) '))
      print('')
      if inp == TR('Yes').lower()[0]:
        if incremental:
          delete_snapshot(snapshot_a)
        else:
          for snap in snaps:
            delete_snapshot(snap)
      else:
        print(TR('Ending cleanup for {0}...').format(subvolume))
        break
      current_idx += 1 if incremental else len(ssnaps)
    print(TR('Finished cleanup for {0}').format(subvolume))

def do_run(args):
  if len(args) < 1:
    log('error', TR('The run command was called without any arguments'))
    return
  import subprocess
  do_mark(args, always_check_remote=True)
  log('all', TR('Wrapping external command: {0}').format(args), flush = True)
  proc = subprocess.run(args)
  do_mark(args)
  sys.exit(proc.returncode)

def tr_dump():
  import re
  r = r'(?s)TR\(\'\'\'(.+?[^\\])\'\'\'|TR\("""(.+?[^\\])"""|TR\(\'(.+?[^\\])\'|TR\("(.+?[^\\])"'
  with open(__file__, 'r') as f:
    print(json.dumps({
      re.sub(r'([^\\])(\\)', r'\1',
        re.sub(r'([^\\])(\\n)', r'\1\n', ''.join(k))):'put your translation here'
      for k in
      re.findall(r, f.read())},
      indent = 2))

def do_help():
  import textwrap
  print('\n' + textwrap.dedent(TR('''
    Usage: prbsync COMMAND [ARG]...

    PRBSync is an event-driven wrapper for Rclone designed around efficiently
    synchronizing files on-demand (e.g. right before opening them or right
    after saving them) by utilizing hooks in various programs rather than using
    a background service or scheduled task. PRBSync makes it easier to define
    what gets synced and when.

    Various usage examples are provided below. See PRBSync's manual for
    additional information including how to setup and configure PRBSync and the
    details of each command.

    prbsync sync

    prbsync mark
    prbsync mark /home/example/cloud/a.txt /home/example/b.txt

    prbsync notify
    prbsync wait_and_notify

    prbsync query
    prbsync json_query
    prbsync tail_log_and_json_query

    prbsync lsf
    prbsync lsf MyCloudDrive

    prbsync auto
    prbsync auto MyCloudDrive
    prbsync silent_auto
    prbsync silent_auto MyCloudDrive

    prbsync auto_sync
    prbsync auto_sync MyCloudDrive
    prbsync silent_auto_sync
    prbsync silent_auto_sync MyCloudDrive

    prbsync run python3 my_script.py

    prbsync clean
    prbsync clean MyCloudDrive

    prbsync iclean
    prbsync iclean MyCloudDrive

    prbsync diff /home/example/snapshots/MyCloudDrive@123
    prbsync diff /home/example/snapshots/MyCloudDrive@123 /home/example/snapshots/MyCloudDrive@456

    prbsync hydrate MyCloudDrive
    prbsync dehydrate MyCloudDrive

    prbsync fix MyCloudDrive

    prbsync log

    prbsync
    prbsync help
    ''')).strip() + '\n')

def do_log():
  log_path = get_config()['log_path']
  if not os.path.exists(log_path):
    print(TR('The log is empty or cannot be found'))
  pager = get_config().get('pager')
  if not pager:
    pager = os.environ.get('PAGER')
  if not pager:
    pager = __import__('shutil').which('less')
  if not pager:
    pager = __import__('shutil').which('more')
  if pager:
    import subprocess, shlex
    return subprocess.run(shlex.split(pager) + [log_path]).returncode
  else:
    acquire_lock()
    if os.name == 'nt':
      acquire_lock(suffix = '-log')
    with open(log_path, 'r') as f:
      for line in f:
        print(line.rstrip())

def main():
  sys.excepthook = handle_exception
  try:
    with open('/proc/self/comm', 'r+') as f:
      f.write('prbsync')
  except (FileNotFoundError, PermissionError):
    pass

  cmd = sys.argv[1] if len(sys.argv) > 1 else None

  if cmd == 'sync':
    manual_sync_paths()
  elif cmd == 'mark':
    paths = sys.argv[2:]
    do_mark(paths)
  elif cmd == 'notify':
    do_notify(False)
  elif cmd == 'wait_and_notify':
    do_notify(True)
  elif cmd == 'query':
    do_query()
  elif cmd == 'json_query':
    do_json_query(log_tailing = False)
  elif cmd == 'tail_log_and_json_query':
    do_json_query(log_tailing = True)
  elif cmd == 'lsf':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_lsf(target)
  elif cmd == 'auto':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_auto(False, False, target)
  elif cmd == 'silent_auto':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_auto(False, True, target)
  elif cmd == 'auto_sync':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_auto(True, False, target)
  elif cmd == 'silent_auto_sync':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_auto(True, True, target)
  elif cmd == 'async_auto':
    run_detached(['silent_auto'])
  elif cmd == 'run':
    args = sys.argv[2:]
    do_run(args)
  elif cmd == 'clean':
    targets = sys.argv[2:]
    do_clean(False, targets)
  elif cmd == 'iclean':
    targets = sys.argv[2:]
    do_clean(True, targets)
  elif cmd == 'diff':
    args = sys.argv[2:]
    do_diff(args)
  elif cmd == 'hydrate':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_hydrate(target)
  elif cmd == 'dehydrate':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_dehydrate(target)
  elif cmd == 'fix':
    target = sys.argv[2] if len(sys.argv) > 2 else None
    do_hydrate(target, skip_local_dir_creation = True)
  elif cmd == 'log':
    sys.exit(do_log())
  elif cmd == 'tr_dump':
    tr_dump()
  elif cmd == 'help' or cmd is None:
    do_help()
  else:
    log('error', TR('Invalid command {0}').format(repr(cmd)))
    if __import__('shutil').which('prbsync') == os.path.abspath(__file__):
      help_cmd = 'prbsync help'
    else:
      help_cmd = __import__('shlex').join((sys.executable, __file__, 'help'))
    log('error', TR('Try {0} for help.').format(repr(help_cmd)))

if __name__ == '__main__':
  main()
