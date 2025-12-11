#!/usr/bin/env python3 

import sys, os, stat, functools

DEFAULT_DIFF_TOOLS = ('diff',)
DIFF_TOOL_ENV_VARS = ('GIT_EXTERNAL_DIFF',)
DEFAULT_PAGERS = ('less', 'more')
PAGER_ENV_VARS = ('PAGER', 'MANPAGER', 'EDITOR')
PRESERVE_ATTR = ('mode', 'ownership', 'timestamps')

def die(error):
  sys.stderr.write(f'{error}'.strip() + '\n')
  sys.stderr.flush()
  sys.exit(1)

HELP_DOC = '''
Usage: diffcp [OPTION]... SOURCE... DESTINATION

diffcp is a cp-like copy utility with two key differences: users are
interactively prompted to confirm each change and all changes must be approved
before any changes are made at the destination and the source file(s) and their
metadata are stored in an in-memory snapshot so that, if the source file(s)
change between when a user approves changes and when the changes are applied, 
the changes they approved will be applied rather than any they haven't had a 
chance to review. This is designed to improve security when copying files from
cloud storage, network drives, etc.

diffcp inherits the following flags from cp:

  -p                               same as --preserve=mode,ownership,timestamps
      --preserve[=ATTR_LIST]       preserve the specified attributes
      --no-preserve=ATTR_LIST      don't preserve the specified attributes
  -f, --force                      if an existing destination file cannot be
                                   opened, remove it and try again
  -R, -r, --recursive              copy directories recursively
          --help                   display this help and exit

diffcp also supports the below unique flags:

  --fail-if-different              fail with a non-zero return code if any
                                   differences between the source(s) and 
                                   destination are found. This can be used in
                                   automated, non-interactive scripts.
  --exclude-pattern [GLOB_PATTERN] exclude any source paths which match the
                                   given pattern. This option can be specified
                                   multiple times.
  --symlink-pattern [GLOB_PATTERN] create a symlink to any source paths which
                                   match the given pattern rather than copying
                                   them. This option can be specified multiple
                                   times.
  --fail-if-destinations-overlap   fail with a non-zero return code if multiple
                                   source paths are being written to the same
                                   destination path
  --ternary-return-code            if specified, returns 0 if changes were 
                                   found and the user approved the changes, 2
                                   if there is no change between the source(s)
                                   and destination and any other value if an 
                                   error occurs or the user aborts the copy.
                                   If not specified, 0 will be returned for 
                                   success and a non-zero value will be 
                                   returned if there are any errors or if the
                                   user aborts. This option can be used in 
                                   scripts to interactively perform a copy and
                                   detect if a copy took place with only a
                                   single call to diffcp.
'''.lstrip()

def die_with_help():
  print(HELP_DOC)
  sys.exit(0)

def matches(path, k, **kwargs):
  patterns = kwargs.get(k)
  if not patterns:
    return False
  import fnmatch
  for pattern in patterns:
    if fnmatch.fnmatch(path, pattern):
      return True
  return False

MODE_MASK = stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO

def update_snapshot_info(snapshot,
                         src,
                         dst,
                         st = None,
                         symlink = False,
                         data = None,
                         **kwargs):
  if symlink:
    info = {'source': src, 'symlink': True}
  else:
    info = {
      'source': src,
      'size': st.st_size,
      'mode': st.st_mode & MODE_MASK,
      'uid': st.st_uid,
      'gid': st.st_gid,
      'mtime': st.st_mtime,
      'mtime': st.st_atime,
    }
  if data is not None:
    info['data'] = data
  first = snapshot.setdefault(dst, info)
  if info is not first and kwargs.get('fail_if_destinations_overlap'):
    die(f'Multiple paths have {dst} as their destination')

def update_snapshot(snapshot, source, destination, is_visited_dir = False, **kwargs):
  try:
    if not is_visited_dir:
      if matches(source, 'exclude_patterns', **kwargs):
        return
      if matches(source, 'symlink_patterns', **kwargs):
        data = None
        st = None
        symlink = True
      else:
        with open(source, 'rb') as f:
          data = f.read()
          st = os.fstat(f.fileno())
        symlink = False
      if kwargs.get('dest_force_dir'):
        dst = os.path.join(destination, os.path.basename(source))
      else:
        dst = destination
      update_snapshot_info(snapshot, source, dst,
                           st = st, symlink = symlink, data = data, **kwargs)
      return
  except IsADirectoryError:
    pass
  if not kwargs.get('recursive'):
    sys.stderr.write(
      f'{sys.argv[0]}: -r not specified; omitting directory {repr(source)}\n'
    )
    sys.stderr.flush()
    return
  with os.scandir(source) as it:
    name = os.path.basename(source)
    if not name:
      name = os.path.basename(os.path.dirname(source))
    root = os.path.join(destination, name)
    if not is_visited_dir:
      update_snapshot_info(snapshot, source, root, os.stat(source), **kwargs)
    for entry in it:
      if matches(entry.path, 'exclude_patterns', **kwargs):
        continue
      symlink = matches(entry.path, 'symlink_patterns', **kwargs)
      if entry.is_file() and not symlink:
        with open(entry.path, 'rb') as f:
          data = f.read()
      else:
        data = None
      update_snapshot_info(snapshot, entry.path, os.path.join(root, entry.name),
                           st = entry.stat(), data = data, symlink = symlink, **kwargs)
      if entry.is_dir() and not symlink:
        update_snapshot(snapshot, entry.path, root, is_visited_dir = True, **kwargs)

def format_size(size):
  b = size
  if size is None:
    return 'None'
  if size < 1024:
    return f'{size} B'
  for u in ('Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi', 'Yi'):
    size /= 1024.0
    if abs(size) < 1024.0 or u == 'Yi':
      return f'{size:3.1f} {u}B ({b} B)'

def format_ts(ts):
  if ts is None:
    return 'None'
  import time
  return time.strftime('%c', time.gmtime(ts))

@functools.cache
def format_uid(uid):
  if uid is None:
    return 'None'
  try:
    import pwd
    name = pwd.getpwuid(uid).pw_name
    return f'{name} ({uid})'
  except (KeyError, ModuleNotFoundError):
    return f'#{uid}'

@functools.cache
def format_gid(gid):
  if gid is None:
    return 'None'
  try:
    import grp
    name = grp.getgrgid(gid).gr_name
    return f'{name} ({gid})'
  except (KeyError, ModuleNotFoundError):
    return f'#{gid}'

def format_info(label, size, mode, mtime, atime, uid, gid, **kwargs):
  fmt = [('Size', format_size(size))]
  if kwargs.get('preserve_mode'):
    fmt.append(('Mode', stat.filemode(mode)))
  if kwargs.get('preserve_timestamps'):
    fmt.append(('Modified Time', format_ts(mtime)))
    fmt.append(('Access Time', format_ts(atime)))
  if kwargs.get('preserve_ownership'):
    fmt.append(('Owning User', format_uid(uid)))
    fmt.append(('Owning Group', format_uid(gid)))
  return '\n'.join((f'{label} {k}: {v}' for k,v in fmt))

def find_tool(env_vars, defaults):
  import shutil
  for v in env_vars:
    tool = os.environ.get(v)
    if tool:
      tool = shutil.which(tool)
    if tool:
      return tool
  for default in defaults:
    tool = shutil.which(default)
    if tool:
      return tool
  vlist = ' or '.join(env_vars)
  dlist = ' or '.join(defaults)
  die(f'Missing a required tool: Please set {vlist} and/or install {dlist}')

def wrap(txt, width):
  buf = []
  for line in txt.splitlines():
    buf += [line[i:i+width] for i in range(0, len(line), width)] or ['']
  return '\n'.join(buf) + '\n'

def diff_snapshot_against_destination_and_enqueue_actions(snapshot, **kwargs):
  difftool = None
  pager = None
  has_chown = hasattr(os, 'chown')
  action_queue = []
  for dst in sorted(snapshot.keys()):
    info = snapshot[dst]
    try:
      st = os.stat(dst, follow_symlinks = False)
    except FileNotFoundError:
      st = None
  
    src_is_symlink = info.get('symlink')
    abs_src = os.path.abspath(info.get('source'))

    dst_is_symlink = st is not None and stat.S_ISLNK(st.st_mode)
    dst_has_data = not dst_is_symlink and st and not stat.S_ISDIR(st.st_mode)
    abs_dst = os.path.abspath(dst)
    dst_link = os.readlink(dst) if dst_is_symlink else None

    if src_is_symlink and dst_is_symlink and dst_link == abs_src:
      continue

    src_mode = info.get('mode')
    src_size = info.get('size')
    src_mtime = info.get('mtime')
    src_atime = info.get('mtime')
    src_uid = info.get('uid')
    src_gid = info.get('gid')
    src_data = info.get('data')
    
    dst_mode = None if dst_is_symlink or st is None else (st.st_mode & MODE_MASK)
    dst_size = None if dst_is_symlink or st is None else st.st_size
    dst_mtime = None if dst_is_symlink or st is None else st.st_mtime
    dst_atime = None if dst_is_symlink or st is None else st.st_atime
    dst_uid = None if dst_is_symlink or st is None else st.st_uid
    dst_gid = None if dst_is_symlink or st is None else st.st_gid
    dst_data = None

    changes = []
    queued_action = {'destination': dst}
    if src_is_symlink:
      queued_action['symlink_target'] = abs_src
    
    if src_size != dst_size and src_data is not None:
      changes.append(('Size', format_size(dst_size), format_size(src_size)))
    if src_mode != dst_mode and kwargs.get('preserve_mode'):
      changes.append(('Mode',
                      None if dst_mode is None else stat.filemode(dst_mode), 
                      None if src_mode is None else stat.filemode(src_mode)))
      queued_action['mode'] = src_mode
    if src_mtime != dst_mtime and kwargs.get('preserve_timestamps'):
      changes.append(('Modified Time', format_ts(dst_mtime), format_ts(src_mtime)))
      queued_action['times'] = (src_atime, src_mtime)
    if src_atime != dst_atime and kwargs.get('preserve_timestamps'):
      changes.append(('Access Time', format_ts(dst_atime), format_ts(src_atime)))
      queued_action['times'] = (src_atime, src_mtime)
    if src_uid != dst_uid and has_chown and kwargs.get('preserve_ownership'):
      changes.append(('Owning User', format_uid(dst_uid), format_uid(src_uid)))
      queued_action['uid'] = src_uid
    if src_gid != dst_gid and has_chown and kwargs.get('preserve_ownership'):
      changes.append(('Owning Group', format_gid(dst_gid), format_gid(src_gid)))
      queued_action['gid'] = src_gid
    if kwargs.get('fail_if_different') and len(changes) > 0:
      sys.exit(1)

    if not changes:
      if dst_has_data:
        with open(dst, 'rb') as f:
          dst_data = f.read()
        if dst_data == src_data:
          continue
      elif src_data is None and st is not None:
        continue

    if kwargs.get('fail_if_different'):
      sys.exit(1)
  
    if src_data is not None:
      queued_action['data'] = src_data
  
    prompt = '\n\n\n'
    if src_is_symlink:
      prompt += f'A symlink to:\n  {abs_src}\n'
      prompt += f'will be created at:\n  {abs_dst}'
    else:
      prompt += f'The source path:\n  {abs_src}\n'
      prompt += f'will be copied to:\n  {abs_dst}'

    if dst_is_symlink:
      prompt += f'\n\nThis will replace the existing symlink at:\n  {abs_dst}\nto:\n  {dst_link}'
      if kwargs.get('force'):
        queued_action['remove_func'] = os.remove
    elif st and stat.S_ISDIR(st.st_mode):
      prompt += f'\n\nThis will replace the existing directory at:\n  {abs_dst}'
      if kwargs.get('force'):
        queued_action['remove_func'] = os.rmdir
    elif st:
      prompt += f'\n\nThis will replace the existing file at:\n  {abs_dst}'
      if kwargs.get('force'):
        queued_action['remove_func'] = os.remove
    elif st is None:
      prompt += f'\n\nThis path does not exist and will be created:\n  {abs_dst}'
  
    for attr, prior, post in changes:
      prompt += f'\n\n{attr} will change from:\n  {prior}\nto:\n  {post}'

    if src_data is not None and dst_has_data:
      prompt += "\n\nThe below changes will be made to the file's contents:\n\n"
      if difftool is None:
        difftool = find_tool(DIFF_TOOL_ENV_VARS, DEFAULT_DIFF_TOOLS)
      import subprocess
      prompt += subprocess.run((difftool, abs_dst, '-'),
                               stdout = subprocess.PIPE,
                               stderr = subprocess.STDOUT,
                               input = src_data
                              ).stdout.decode()
    elif src_data:
      prompt += '\n\nThe below content will be written at the destination:\n\n'
      prompt += src_data.decode()
    elif dst_has_data:
      if dst_data is None:
        with open(dst, 'rb') as f:
          dst_data = f.read()
      if dst_data:
        prompt += '\n\nThe below content at the destination will be lost:\n\n'
        prompt += dst_data.decode()

    while True:
      try:
        width, height = os.get_terminal_size()
      except OSError:
        width, height = 80, 24
      wprompt = wrap(prompt, width)
      use_pager = len(wprompt.splitlines()) > (height - 3)
      if use_pager:
        if pager is None:
          pager = find_tool(PAGER_ENV_VARS, DEFAULT_PAGERS)
        import subprocess
        subprocess.run((pager,), input = prompt.encode())
      else:
        print(wprompt)
      print('')
      print('Would you like to proceed with these changes?')
      if use_pager:
        inp = input('(Y)es / (N)o / (V)iew again ')
      else:
        inp = input('(Y)es / (N)o ')
      inp = inp.lower().strip()
      if inp == 'y':
        action_queue.append(queued_action)
        break
      if inp == 'n':
        die('Copy aborted')
  return action_queue

def performed_enqueued_copy(action, **kwargs):
  destination = action['destination']
  remove_func = action.get('remove_func')
  symlink_target = action.get('symlink_target')
  mode = action.get('mode')
  uid = action.get('uid', -1)
  gid = action.get('gid', -1)
  if symlink_target:
    is_dir = os.path.isdir(symlink_target) if os.name == 'nt' else False
    try:
      os.symlink(symlink_target, destination, target_is_directory = is_dir)
    except OSError as err:
      if remove_func is not None:
        remove_func(destination)
        os.symlink(symlink_target, destination, target_is_directory = is_dir)
      else:
        raise err
    if mode is not None:
      os.chmod(destination, mode, follow_symlinks = False)
  else:
    data = action.get('data')
    if data is not None:
      for i in range(2):
        try:
          with open(destination, 'wb') as f:
            f.write(data)
            if mode is not None:
              os.fchmod(f.fileno(), mode)
            if uid != -1 or gid != -1:
              os.chown(f.fileno(), uid, gid)
        except OSError as err:
          if i == 0 and remove_func is not None:
            remove_func(destination)
          else:
            raise err
    else:
      try:
        os.mkdir(destination, mode = 0o777 if mode is None else mode)
      except OSError as err:
        if remove_func is not None:
          remove_func(destination)
          os.mkdir(destination, mode = 0o777 if mode is None else mode)
        else:
          raise err
  if symlink_target or not data and (uid != -1 or gid != -1):
    os.chown(destination, uid, gid, follow_symlinks = False)
  times = action.get('times')
  if times is not None:
    os.utime(destination, times = times, follow_symlinks = False)

def main():
  sources = []
  kwargs = {
    'exclude_patterns': [],
    'symlink_patterns': [],
  }
  to_read = None

  for arg in sys.argv[1:]:
    if to_read:
      kwargs[to_read].append(arg)
      to_read = None
    elif arg == '--preserve':
      for attr in PRESERVE_ATTR:
        kwargs[f'preserve_{attr}'] = True
    elif arg.startswith('--preserve='):
      for attr in arg[11:].split(','):
        if attr not in PRESERVE_ATTR:
          die(f'Invalid attribute: {repr(attr)}')
        kwargs[f'preserve_{attr}'] = True
    elif arg.startswith('--no-preserve='):
      for attr in arg[14:].split(','):
        if attr not in PRESERVE_ATTR:
          die(f'Invalid attribute: {repr(attr)}')
        kwargs[f'preserve_{attr}'] = False
    elif arg == '--force':
      kwargs['force'] = True
    elif arg == '--recursive':
      kwargs['recursive'] = True
    elif arg == '--fail-if-different':
      kwargs['fail_if_different'] = True
    elif arg == '--exclude-pattern':
      to_read = 'exclude_patterns'
    elif arg == '--symlink-pattern':
      to_read = 'symlink_patterns'
    elif arg == '--fail-if-destinations-overlap':
      kwargs['fail_if_destinations_overlap'] = True
    elif arg == '--ternary-return-code':
      kwargs['ternary_return_code'] = True
    elif arg == '--help':
      die_with_help()
    elif arg.startswith('--'):
      die(f'Invalid Flag: {arg}')
    elif arg.startswith('-'):
      for a in arg[1:]:
        if a == 'f':
          kwargs['force'] = True
        elif a in 'rR':
          kwargs['recursive'] = True
        elif a == 'p':
          for attr in PRESERVE_ATTR:
            kwargs[f'preserve_{attr}'] = True
        else:
          die(f'Invalid flag -{a}')
    else:
      sources.append(arg)

  if len(sources) < 2:
    die_with_help()

  if to_read:
    die(f'Missing Value: {to_read}')

  destination = os.path.abspath(sources[-1])
  sources = sources[:-1]
  if len(sources) > 1:
    kwargs['dest_force_dir'] = True

  snapshot = {}
  for source in sources:
    update_snapshot(snapshot, source, destination, **kwargs)

  action_queue = diff_snapshot_against_destination_and_enqueue_actions(snapshot, **kwargs)

  for action in action_queue:
    performed_enqueued_copy(action, **kwargs)

  if kwargs.get('ternary_return_code') and len(action_queue) < 1:
    sys.exit(2)

if __name__ == '__main__':
  main()

