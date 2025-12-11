# PRBSync

PRBSync is an event-driven wrapper for Rclone designed around efficiently synchronizing files on-demand by utilizing hooks in various programs rather than using a background service or scheduled task. PRBSync makes it easy for you to define what gets synced and when. PRBSync extends Rclone adding new, configurable functionality to it including:

  - utilities which make it easier to setup hooks (e.g. sync before running certain scripts or programs, sync after saving files, etc)
  - the ability to use filters to define which files are automatically synched and which are manually synched
  - automatic snapshots on BTRFS file systems
  - snapshot diffing and cleanup utilities
  - the ability to "hydrate" and "dehydrate" paths, enabling you to quickly create synchronized, local copies of remote paths and later remove those local copies while retaining the remote copy in order to reclaim local storage space as needed

PRBSync should run anywhere Rclone runs, however, is has only been extensively tested on Linux.
  

## Installation

1. Install [Python](https://www.python.org) and [Rclone](https://rclone.org). The exact commands to do this will vary based on your OS but some examples are provided below.

Arch Linux:
````
pacman -Syu python rclone
````

Windows:
````
winget install python Rclone.Rclone
````

Termux:
````
pkg install python rclone
````

2. Setup at least one Rclone remote per [Rclone's manual](https://rclone.org/commands/rclone_config_create). Ensure that the remotes are listed as compatible with bisync under the ["Supported backends" section of the bisync page](https://rclone.org/bisync/#supported-backends).

````
rclone create mydrive drive
````

2. Download PRBSync either by using git or by downloading and extracting the latest archive in [Releases](https://github.com)

````
git clone TODO
````

3. Copy prbsync to one of the directories in your [PATH](https://en.wikipedia.org/wiki/PATH_(variable)#firstHeading) or create a new dirctory and add it to PATH. You can use `echo $PATH` or `echo %PATH%` to check what the existing directories are. Some possible examples include:

````
/usr/bin/prbsync
/bin/prbsync
$HOME/.local/bin/prbsync
%LOCALAPPDATA%\Programs\PRBSync\prbsync
````

If you're on Windows, create a file called `prbsync.bat` in your PRBSync installation folder and copy the below two lines into it:
````
@echo off
py %~dp0prbsync %*
````

4. Create your prbsync.toml configuration file. See the [Configuration](#Configuration) section for details.

5. PRBSync is now setup and you can manually trigger a sync by running `prbsync sync`. See the [Usage](#Usage) section below for a full rundown of commands offered by PRBSync. By default, PRBSync will only perform a sync when one is manually triggered via the `sync` command. To fully use PRBSync, see the [Aliases, Hooks and Triggers](#Aliases-Hooks-and-Triggers) section regarding automating when PRBSync runs.


## Uninstallation

Run `prbsync query` to list the state file path, log file path, snapshot directory and config file path. Delete each of these paths then delete prbsync (e.g. /usr/bin/prbsync or %LOCALAPPDATA%\Programs\PRBSync) itself to fully uninstall PRBSync. If you have setup any aliases, hooks or triggers for PRBSync, you will want to remove those as well.

## Configuration

PRBSync stores its configuration in a TOML file, named prbsync.toml by default. PRBSync will first look for this file in the path specified by the PRBSYNC_CONFIG environment variable. If this variable is not set, it will look in the paths below in the order they are given:

````
$XDG_CONFIG_HOME/prbsync.toml
%APPDATA%\prbsync.toml
~/.config/prbsync.toml
````

PRBSync has default values for each of its settings. If a setting is not specified, the default value will be used. Once you've setup prbsync, you can run `prbsync json_query` to list all of the current settings as well as the paths PRBSync is using for the configuration, state, log and snapshot files.

PRBSync works by using a user provided list of syncable paths with each path containing a local path, remote path and an optional filter to use for automatic synching. You must specify at least one `syncable_path` within prbsync.toml. This is the only setting that is mandatory. The rest can be left at their default values. Below is an example of a minimal prbsync.toml:

````
[syncable_paths.MyCloudDrive]
local_path = '~/MyDrive'
remote_path = 'MyDrive:'
````

`local_path` is a local directory that your files will be synched to. It must already exist. `remote_path` is the remote that Rclone will use. It's must match the name of a remote that you have already configured in Rclone and its path, if given, must be one that exists on that remote. The `MyCloudDrive` portion of `syncable_paths.MyCloudDrive` is the name of the syncable path. This name is only ever used by PRBSync (e.g. in commands to specify a specific path or in logs when recording which path is being synchonized). The syncable path's name can be anything as long as it is unique to any other syncable path's name. It does not have to match the remote name in Rclone.

If you want to be able to automatically synchronize a path, you must also specify an `auto_sync_filter`. When performing auto syncs, PRBSync will skip any paths without an `auto_sync_filter`. The syntax used for the filter is the same as what Rclone uses for its filters. See the [Filtering page](https://rclone.org/filtering) in Rclone's manual. Below is an example of a syncable path with an `auto_sync_filter`. The filter will only auto sync files with the `.jpg` or `.png` extensions. A manual sync will be needed to sync any other changes. See [Usage](#Usage) for additional details regarding auto syncing.

````
[syncable_paths.MyCloudDrive]
local_path = '~/MyDrive'
remote_path = 'MyDrive:'
auto_sync_filter = [
  '+ **.{png,jpg}',
  '- **',
]
````

Various other, optional options exist which can be used to modify the behavior of PRBSync's commands. See the [Usage](#Usage) section for a description of when and how to use commands, including those referenced in this section. The following options are available:


 - **alert_sound** - The path to a sound file which will be played along with the toast notification produced by `wait` and `wait_and_notify`.
 - **play_alert_sound_with_notification** - Turns the alert sound on or off. 
 - **alert_tone** - The argument passed to ffplay which will be used to generate a tone which will be played along with the toast notification produced by `wait` and `wait_and_notify`
 - **play_alert_tone_with_notification** - Turns the alert tone on or off.
 - **custom_notification_command** - A command which will be run when `notify` or `wait_and_notify` trigger a notification. It can be used to display a notification or play a sound.

 - **display_notification_after_manual_sync** - If true, will display a notification after manual syncs are complete for all paths
 - **display_notification_after_auto_sync** - If true, will display a notification after an auto sync is done

 - **log_path** - The path log messages will be written to
 - **log_level** - The current log level. Valid options are listed below. Note that log messages with the level `silent` are never printed to the console.
   - `all` will log everything
   - `auto` will only log automatically run commands, silent commands and errors
   - `silent` will only log silent commands and errors
   - `error` will only log errors
   - `none` will log nothing

 - **max_time_between_syncs** - The maximum time in seconds between manual syncs before a manual sync will be automatically marked as due 

 - **create_snapshots**: Controls whether snapshots will be taken before each sync. Note that your `local_path`s must be BTRFS subvolumes in order for snapshots to be taken.
 - **snapshot_path** - The directory snapshots will be stored in
 - **snapshots_to_keep** - The number of recent snapshots `clean` and `iclean` will keep
 - **disk_usage_path**: The path for which disk usage stats will be collected before `clean` and `iclean`. This should be the same disk snapshots are stored on. Stats are collected before snapshots are deleted to make it easier to tell how much disk space was reclaimed. Using a path for an incorrect path will cause the wrong stats to be displayed but will not cause any other issues.
 - **hash_buffer_size**: The size in bytes of the buffer used when hashing files for snapshot diffs. Larger values have faster performance but use more memory.

 - **launch_background_notifer_after_mark** - Controls whether calling `mark` will also trigger a background instance of `wait_and_notify`
 - **log_stable_wait** - Controls whether `wait_and_notify` will log its status to the log
 - **initial_stable_wait_delay** - The initial duration in seconds for how long `wait_and_notify` should wait before checking if all of the paths are stable
 - **max_stable_wait_delay**: The maximum duration in seconds for how long `wait_and_notify` should wait before checking if all of the paths are stable
 - **stable_wait_backoff_rate** - The rate at which the duration increases if the paths aren't stable
 - **stable_wait_iterations** - The number of times the paths must be stable in a row before `wait_and_notify` will display a notification

 - **state_path**: The path to PRBSync's state file. The state file is used to record the times of syncs, whether a sync is due, etc.
 - **syncable_paths**: An array of syncable paths. See above.


## Usage

PRBSync is always invoked using the pattern `prbsync command_name optional_arg1 optional_arg2 ...`

The below commands are available. Examples are given for each command and, where applicable, each variation of each command:

`prbsync sync` - Manually runs `rclone bisync` for all syncable paths. See [Rclone's manual](https://rclone.org/bisync) for more information on how bisync works and how to set it up. The `sync` command is designed to be manually triggered by a user so that they can review changes made to their files; see the `auto` commands below for automatic file syncing.

`prbsync mark` - Mark a manual sync as being due. `prbsync wait_and_notify` will also run in the background.

`prbsync mark /home/example/cloud/a.txt /home/example/b.txt` - Iterates through the given list of one or more paths. Paths that are not within syncable paths are ignored. If a path matches the `auto_sync_filter` for its syncable path, an auto sync will be performed for all auto sync eligible files in the syncable path. If a path cannot be auto synced, a manual sync will be marked as due. `prbsync wait_and_notify` will also run in the background. See the documentation for the `auto` command for details on auto synching.

`prbsync notify` - Prints a message to the console and attempts to display a toast notification if a manual sync is due and does nothing if a sync isn't due. The return code will be 0 if a sync is due and 1 if not.

`prbsync wait_and_notify` - Waits until the syncable paths are "stable" (i.e. no syncs or marks have occurred for an extended period of time) then prints a message to the console and attempts to display a toast notification if a manual sync is due. Only one instance of `wait_and_notify` can run per user. The return code will be 0 if a sync is due and 1 if not. If another instance is still running, `wait_and_notify` will immediately exit silently without doing anything.

`prbsync query` - Displays PRBSync's status in a human readable format. This command can be used to check if a manual sync is due, when the last auto and manual syncs were run for each path as well as other status information. The return code will be 0 if a sync is due and 1 if not.

`prbsync json_query` - Retrieves the state and configuration of PRBSync and prints it as a json object. This command can be used to pragmatically check PRBSync's status when running PRBSync via sudo, ssh or in other situations where its status needs to be serialized. The json object will be 'pretty printed' for easier human reading so this command can also be used to confirm what the current settings and state are. The return code will be 0 if a sync is due and 1 if not.

`prbsync lsf` - Print all local files matching the `auto_sync_filter` for each syncable path.

`prbsync lsf MyCloudDrive` - Print all local files matching the `auto_sync_filter` for the syncable path with the given name. If no filter has been set, no paths will be listed. `lsf` is designed to make it easier to test filters to make sure they are including/excluding the correct files before attempting an auto sync.

`prbsync auto` - Performs an auto sync on all syncable paths which have an `auto_sync_filter`. Only files that match the filter will be synced. Auto sync will only upload local files and download remote files which were modified after the last sync and which have not been modified in both locations. New files and modified files will auto sync. Folders and deleted files will not auto sync. If auto sync encounters a change that it cannot handle automatically, a manual sync will be marked as due. In order to save bandwidth and avoid rate-limiting, `prbsync auto` will only check the remote path for changes if local changes are found. Although auto synching is bidirectional, `prbsync auto` is mostly designed to be run after files have been modified (or you suspect they may have been modified) to upload changes. If you want to download remote changes before opening a file, see `prbsync auto_sync`.

**IMPORTANT**: PRBSync will ensure that auto syncs remain [thread safe](https://en.wikipedia.org/wiki/Thread_safety) even if they are triggered by multiple processes as long as all of the syncs are performed on the same device. If you have multiple devices, be careful when choosing the conditions under which auto sync will run and when creating your `auto_sync_filter` as, if two devices perform an auto sync of the same file at the same time, one will overwrite the other's changes.

`prbsync auto MyCloudDrive` - Performs an auto sync for the syncable path with the given name

`prbsync silent_auto` and `prbsync silent_auto MyCloudDrive` - Same as auto but output will not be written to the console unless errors occur. Output may be written to the log depending on the configured log level.

`prbsync auto_sync` and `prbsync auto_sync MyCloudDrive` - Same as auto but auto_sync always checks the remote path for changes even if no local changes are found. auto_sync is designed to be run before opening or reading from files in a syncable path. If you only need to upload local changes to the remote path, consider using `prbsync mark` or `prbsync auto` instead.

`prbsync silent_auto_sync` and `prbsync silent_auto_sync MyCloudDrive` - Same as auto_sync but output will not be written to the console unless errors occur. Output may be written to the log depending on the configured log level.

`prbsync run python3 my_script.py` - `run` iterates through all of the arguments passed to it and, if any are paths within a syncable path, run will auto sync the path while downloading any remote changes (like `auto_sync` does). After changes have been synchronized, the given command will be run. After the command has completed, another auto sync will run and local changes to any of the syncable local paths will be uploaded. This is a convenience function which essentially handles calling `auto_sync` and `auto` for you before and after running a command while also automatically detecting and limiting which paths to sync. Note that only arguments where the entire argument is a valid path will be checked so arguments such as `--somearg=/home/example/cloud/a.txt` will not be synched even if `/home/example/cloud` is the local path of a syncable path. In those cases or in other cases where paths cannot be automatically determined (such as paths passed in through environment variables, paths passed through stdin, etc) you will need to either manually call `auto_sync` and `auto` or create your own wrapper.

`prbsync clean` and `prbsync clean MyCloudDrive` - On systems with support for BTRFS, PRBSync will attempt to create a snapshot of the local path for each syncable path before syncing files so that any undesired changes can be diffed or rolled back easily. Snapshots are only deleted when the `clean` or `iclean` commands are manually run. The `snapshots_to_keep` setting controls how many snapshots are kept and PRBSync will keep the most recent snapshots of that number. When clean if run, you will be shown a diff of the oldest snapshot and the oldest snapshot being kept for each syncable path and interactively asked if you want to delete the old snapshots. The name or names of one or more syncable paths can be passed as arguments to limit cleanup to those paths.

`prbsync iclean` and `prbsync iclean MyCloudDrive` - `iclean` is almost identical to `clean`, however, `iclean` does snapshot diffs and deletion incrementally. `iclean` will show you the diff of the oldest two snapshots and will interactively ask if you want to delete the oldest snapshot. This will continue until only `snapshots_to_keep` snapshots remain or until the user chooses not to delete a snapshot. This enables more fine grained inspection of snapshots before deletion.

`prbsync diff /home/example/snapshots/MyCloudDrive@123` and `prbsync diff /home/example/snapshots/MyCloudDrive@123 /home/example/snapshots/MyCloudDrive@456` - Prints the diff of two snapshots or one snapshot and the current local path

`prbsync hydrate MyCloudDrive` - "Hydrates" or "re-hydrates" a given path. This will create a local copy of an existing remote path. This can be used to setup bisync as an alternative to `rclone bisync --resync` however, note that PRBSync cannot do dry runs so, if you need to do a dry run, you should set up synching by calling `rclone bisync` manually. The return code will be 0 if the given path is successfully hydrated and 1 if any errors occur. This command will return 1 if the path is already hydrated or if anything already exists at the local path you are trying to hydrate.

`prbsync dehydrate MyCloudDrive` - "Dehydrates" the specified path, synchronizing it with the remote path and removing the local copy of the path while retaining the remote copy. Returns 0 if the command succeeds and 1 if any errors occur. This command will return 1 if the specified path has not been hydrated. Note that `dehydrate` will run a manual sync of the path to ensure that all changes are synched to the remote copy before deleting the path so, as with `prbsync sync`, you will likely want to make sure that you only call the command in situations where you are able to review its output. Also, `dehydrate` will not remove any snapshots associated with the path; you must remove snapshots manually  e.g by using `prbsync clean MyCloudDrive` to bulk delete old snapshots and `btrfs subvolume list /` and `btrfs subvolume delete /path/to/some/snapshots@1717237272` to remove each recent snapshot. `hydrate` and `dehydrate` are designed to make it easy to quickly create and remove synchronizable offline copies of paths on storage constrained devices such as phones and tablets. It is strongly recommended that you set `create_snapshots` to `false` in your `prbsync.toml` if you are running PRBSync on such a device as this will prevent snapshots from using up your limited storage space and save you from having to later delete those snapshots. If you do not need offline access to your paths and your device supports FUSE, `rclone mount` may be a better alternative.

**IMPORTANT**: The `hydrate` and `dehydrate` commands run atomically so you can safely simultaneously call either of them on the same path from multiple scripts or multiple instances of the same script to ensure the path exists or has been removed, however, note that PRBSync does not hold any locks between commands so, for example, if you have a script or multiple scripts which hydrate a path, do something with that path then dehydrate the path, you will need to make sure that one instance of the script does not dehydrate the path while another is using it, either by adding locks to your script(s) or by just manually ensuring you don't run two instances of your scripts at the same time.

`prbsync` and `prbsync help` - Running PRBSync without a command or with the `help` command will show a brief help message.


## Aliases, Hooks and Triggers

A full rundown of all of the commands is given in the [Usage](#Usage) section, however, the general idea is that `prbsync auto_sync` is used to automatically pull remote changes before opening files, `prbsync auto` is used to push local changes after modifying or creating files, `prbsync mark` is used to mark manual syncs as due for files that cannot be auto synced and `prbsync notify` is used to display a notification if a manual sync is due. When, or even if, these commands are called is up to you. You will want to configure your system and various programs depending on your use cases, however, some examples are given below.

You can specify aliases in your shell's auto run script to further shorten PRBSync's commands. Some examples for Linux, OSX and other POSIX systems are given below. The exact script you will want to add them to will vary based on your OS and shell (e.g. Bash uses .bashrc). See [Windows Command Prompt AutoRun](#Windows-Command-Prompt-AutoRun) if you are on Windows.

````
alias bync=prbsync sync
alias bquery=prbsync query
````

You may also want to update the script to launch `prbsync notify` in the background so you will be notified if there is a pending manual sync when you open a new shell.

For Linux/OSX/POSIX:
````
(prbsync notify &)
````

For Windows:
````
start /b prbsync notify
````

Within scripts, you can use `prbsync auto_sync` or `prbsync silent_auto_sync` to pull new changes from the remote to your local files before opening files and/or use `prbsync auto` or `prbsync silent_auto` to push local changes to the remote after modifying files.

For Linux/OSX/POSIX:
````
# Pull changes before command
[ -n "$(type -p prbsync)" ] && prbsync auto_sync

some_command

# Pus changes after command
[ -n "$(type -p prbsync)" ] && prbsync auto
````

For Windows:
````
REM Pull changes before command
WHERE prbsync >nul 2>nul
IF %ERRORLEVEL% EQU 0 prbsync auto_sync

some_command

REM Push changes after command
WHERE prbsync >nul 2>nul
IF %ERRORLEVEL% EQU 0 prbsync auto
````

You can have PRBSync pull in remote changes when you login. If you're using Systemd, you can create a service file in `~/.config/systemd/user/prbsync-auto-sync.service` with these contents:

````
[Unit]
Description=PRBSync Auto Sync at Login
After=graphical-session.target
Wants=graphical-session.target

[Service]
Type=oneshot
ExecStart=prbsync silent_auto_sync
Restart=no

[Install]
WantedBy=graphical-session.target
````

Then run `systemctl --user enable prbsync-auto-sync.service` to enable the service.

If you are using the editor [Kate](https://kate-editor.org), you can set PRBSync to push changes after every save by copying the included configuration file `prbsync_mark_kate.ini` to `${XDG_CONFIG_HOME:-$HOME/.config}/kate/externaltools/` and restarting Kate if it is open.

If you are using the editor [Micro](https://micro-editor.github.io), you can set PRBSync to push changes after every save by installing the included Micro plugin. Copy the included folder `micro-prbsync-plugin` to `~/.config/micro/plug/`.

In KDE Plasma, you can create a keyboard shortcut to run a manual sync by adding the below lines to the end of `${XDG_CONFIG_HOME:-$HOME/.config}/kglobalshortcutsrc`. Replace `Meta+Shift+B` with your preferred shortcut.
````
[services][prbsync_sync.desktop]
_launch=Meta+Shift+B
````

Then create `${XDG_DATA_HOME:-$HOME/.local/share}/applications/prbsync_sync.desktop` with the below contents. Note that the `Exec` line assumes Konsole is installed. Update it accordingly if you don't have Konsole installed or prefer another terminal emulator.
````
[Desktop Entry]
Exec=konsole -p tabtitle='PRBSync' --hold -e prbsync sync
Name=PRBSync Sync
NoDisplay=true
StartupNotify=false
Type=Application
X-KDE-GlobalAccel-CommandShortcut=true
````

These are just a couple of examples to get you started. Look into your favorites programs documentation to see how to add hooks or triggers to them and configure them to call `prbsync auto_sync`, `prbsync auto` and `prbsync mark /path/to/modified/file/here` when appropriate.


## Windows Command Prompt AutoRun

By default, the Windows command prompt does not have a script it automatically runs, such as Bash's .bashrc, however, you can configure it to.

To start, create a script in a path of your choosing. These instructions use `%APPDATA%\autorun.bat`. An example `autorun.bat` is provided below.

````
@echo off

DOSKEY bync=prbsync sync $*
DOSKEY bquery=prbsync query $*
DOSKEY bmark=prbsync mark $*
````

Then run `reg add "HKCU\Software\Microsoft\Command Processor" /v AutoRun /d "%APPDATA%\autorun.bat" /t REG_SZ /f` to set the autorun.bat to run in each new command prompt you create. Open a new command prompt and verify that everything is working as expected.

You can check the currently set autorun script via `reg query "HKCU\Software\Microsoft\Command Processor"` and remove it via `reg delete "HKCU\Software\Microsoft\Command Processor"`

Note that you cannot use a doskey macro within another macro. For example, given the `bmark` macro defined above, another macro such as `DOSKEY bmarkexample=bmark example.txt` would not be valid; you would have to write it as `DOSKEY bmarkexample=prbsync mark example.txt` instead.

 
## Localization

PRBSync has support for non-English languages, however, no translations for it currently exist. Please open a pull request or issue if you are interested in translating it. The command `prbsync tr_dump > prbsync.en_US.json`, with `en_US` replaced with the corresponding [locale name](https://www.gnu.org/software/libc/manual/html_node/Locale-Names.html), can be used to dump all of the translatable strings in PRBSync to a translatable file. You can then open the generated JSON file in your preferred text editor to specify what the translated string should be for each English string. PRBSync will try to load a translation file from the path specified by the `translation_file` setting in `prbsync.toml`. If that fails, it will then look for a path specified by `translation_dir` in `prbsync.toml` and attempt to find a translation file in the below paths in the given order where `en_US` is replaced by the user's auto-detected locale. If no matching translation files are found, PRBSync will fall back to English.

````
{translation_dir}/en_US/prbsync.json
{translation_dir}/prbsync.en_US.json
/usr/share/locale/en_US/LC_MESSAGES/prbsync.json
/usr/share/locale/en_US/prbsync.json
/usr/share/locale/prbsync.en_US.json
{PRBSync install directory}/locale/en_US/prbsync.json
{PRBSync install directory}/prbsync.en_US.json
{PRBSync install directory}/en_US/prbsync.json
{current working directoy}/locale/en_US/prbsync.json
{current working directoy}/prbsync.en_US.json
{current working directoy}/en_US/prbsync.json
````


