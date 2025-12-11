#!/usr/bin/env python3 

import sys, os, tomllib, json, hashlib, stat

IGNORED_STATS = {'atime', 'atime_ns', 'dev',}

def die(error):
  sys.stdout.write(f'Fatal Error: {error}\n')
  sys.stdout.flush()
  sys.exit(-1)

def resolve_path(path):
  old_path = path
  while True:
    path = os.path.expanduser(path)
    path = os.path.expandvars(path)
    path = os.path.abspath(path)
    if path == old_path:
      return path
    old_path = path

def scan_entrypoint(entrypoint_name, entrypoint_config):
  state = {}
  dirs_to_check = entrypoint_config.get('directories_to_check', [])
  cwd = entrypoint_config.get('cwd')
  if cwd:
    dirs_to_check.append(cwd)
  dirs_to_check = set(map(resolve_path, dirs_to_check))
  if len(dirs_to_check) < 1:
    die(f'No Directories to Check Specified for {entrypoint_name}')
  ignored_paths = entrypoint_config.get('ignored_paths', [])
  ignored_paths = set(map(resolve_path, ignored_paths))
  ignored_paths.update(set(map(os.path.dirname, ignored_paths)))
  for dir_to_check in dirs_to_check:
    for root, dirs, files in os.walk(dir_to_check):
      for i in dirs + files:
        path = os.path.join(root, i)
        st = os.stat(path)
        st = {i[3:]: getattr(st, i) for i in 
              filter(lambda i: i.startswith('st_'), dir(st))}
        if not stat.S_ISDIR(st['mode']):
          with open(path, 'rb') as fh:
            st['sha3_512'] = hashlib.file_digest(fh, 'sha3_512').hexdigest()
        st = {k: st[k] for k in (set(st.keys()) - IGNORED_STATS)}
        if path in state:
          die(f'Overlapping Paths: {path} was found in at least two of the specified paths')
        if path not in ignored_paths:
          state[path] = st
  return state

def diff_states(old_state, new_state):
  differences = []
  if old_state is None:
    old_state = {}
  old_paths = set(old_state.keys())
  new_paths = set(new_state.keys())
  for path in sorted(old_paths - new_paths):
    differences.append(f'{path} was removed')
  for path in sorted(new_paths - old_paths):
    differences.append(f'{path} was added')
  for path in sorted(new_paths.intersection(old_paths)):
    old_st = old_state.get(path)
    new_st = new_state.get(path)
    for k in sorted(set(new_st.keys()).union(set(old_st.keys()))):
      old_v = old_st.get(k)
      new_v = new_st.get(k)
      if old_v != new_v:
        differences.append(f'{path}: {k} changed from {old_v} to {new_v}')
  return differences

def main():
  selected_entrypoint = None
  show_help = False
  do_capture = False
  show_captured_paths = False
  scan_path = None
  
  args = list(reversed(sys.argv[1:]))
  if len(args) < 1:
    show_help = True
  while len(args) > 0:
    arg = args.pop()
    if arg in ('-h', '--help'):
      show_help = True
      break
    if arg in ('-c', '--capture'):
      do_capture = True
      continue
    if arg in ('-s', '--show'):
      show_captured_paths = True
      continue
    if arg in ('-d', '--scan'):
      try:
        scan_path = args.pop()
      except IndexError:
        die(f'no path specified for arg {arg}')
      continue
    if arg.startswith('--') or arg[0] == '-':
      die(f'invalid argument: {arg}')
    selected_entrypoint = arg
    break
  args = list(reversed(args))

  if show_help:
    switches = '[-c|--capture] [-s|--show] [-d|--scan dir] [-h|--help]'
    print(f'usage: hashexec {switches} [entrypoint] [args ...]')
    print('see the man page for additional help')
    return

  config_path = os.environ.get('HASHEXEC_CONFIG')
  if not config_path:
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config_home:
      config_path = os.path.join(xdg_config_home, 'hashexec.toml')
  if not config_path:
    appdata = os.environ.get('APPDATA')
    if appdata:
      config_path = os.path.join(appdata, 'hashexec.toml')
  if not config_path:
    config_path = os.path.expanduser(
      os.path.join('~', '.config', 'hashexec.toml'))

  try:
    with open(config_path, 'rb') as f:
      config = tomllib.load(f)
  except FileNotFoundError:
    die('No config file found')

  entrypoints = config.get('entrypoints', [])
  if len(entrypoints) < 1:
    die('No entrypoints specified in config')

  state_path = config.get('state_path')
  if not state_path:
    state_home = os.environ.get('XDG_STATE_HOME')
    if not state_home:
      state_home = os.environ.get('LOCALAPPDATA')
    if not state_home:
      state_home = os.path.expanduser(os.path.join('~', '.local', 'state'))
    state_path = os.path.join(state_home, 'hashexec.json')

  try:
    with open(state_path, 'r') as f:
      state = json.load(f)
  except FileNotFoundError:
    state = {}

  if do_capture:
    try:
      os.remove(state_path)
    except FileNotFoundError:
      pass
    print('Capturing latest state of all entrypoints...')
    for entrypoint_name, entrypoint_config in entrypoints.items():
      old_ep_state = state.get(entrypoint_name)
      new_ep_state = scan_entrypoint(entrypoint_name, entrypoint_config)
      if old_ep_state is None:
        print(f'  {entrypoint_name} is a new entrypoint')
      for diff in diff_states(old_ep_state, new_ep_state):
        print(f'  {diff}')
      state[entrypoint_name] = new_ep_state
    with open(state_path, 'x') as f:
      json.dump(state, f)
    print('Finished capturing the updated state(s)')
    if selected_entrypoint is None:
      return

  if show_captured_paths:
    print('Dumping latest captured states...')
    for entrypoint_name in sorted(state.keys()):
      ep_state = state[entrypoint_name]
      print(f'State for {entrypoint_name}:')
      for path in sorted(ep_state.keys()):
        metadata = ep_state[path]
        print(f'  {path}')
        for k in sorted(metadata.keys()):
          print(f'    {k} = {metadata[k]}')
    print('Finished dumping all captured states.')
    if selected_entrypoint is None:
      return

  if scan_path is not None:
    import pprint
    state = scan_entrypoint('stdin', {'cwd': scan_path})
    pprint.pp(state, sort_dicts = True)
    if selected_entrypoint is None:
      return

  if not selected_entrypoint:
    die('No entrypoint specified')

  entrypoint_config = entrypoints.get(selected_entrypoint)
  if not entrypoint_config:
    die(f'No valid config for entrypoint \'{selected_entrypoint}\'')

  old_ep_state = state.get(selected_entrypoint)
  if not old_ep_state:
    die(f'No captured state found for {selected_entrypoint}')
  current_ep_state = scan_entrypoint(selected_entrypoint, entrypoint_config)
  diffs = diff_states(old_ep_state, current_ep_state)
  if len(diffs) > 0:
    die(f'State modified since last capture for {selected_entrypoint}:\n  ' +
        ('\n  '.join(diffs)))

  cwd = entrypoint_config.get('cwd')
  if cwd:
    cwd = resolve_path(cwd)
  else:
    cwd = None

  cmd = entrypoint_config.get('cmd')
  if not cmd:
    die(f'{selected_entrypoint} is missing a command')
  if type(cmd) is not list:
    import shlex
    cmd = shlex.split(cmd)
  cmd += args

  execvp = getattr(os, 'execvp', None)
  if execvp:
    if cwd:
      os.chdir(cwd)
    execvp(cmd[0], cmd)
  else:
    proc = subprocess.run(cmd, cwd = cwd)
    sys.exit(proc.returncode)

if __name__ == '__main__':
  main()
