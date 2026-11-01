NOVA3 reconstruction worker — DeepSeek Harness loop
==================================================

This download runs an unattended AI worker on the N.O.V.A. 3 source
reconstruction project. It contains NO game data, NO game binaries and NO API
keys. It is not a playable game.

PICK YOUR DOWNLOAD
------------------
  Nova3-Linux-x64.tar.gz        Linux x86_64
  Nova3-Mac-AppleSilicon.zip    macOS on M1/M2/M3/M4
  Nova3-Mac-Intel.zip           macOS on Intel
  Nova3-Windows.zip             Windows 10/11 x86_64
  nova3-source.tar.gz / .zip    any platform, no compiled launcher (needs
                                Python 3.10+; use this if the other routes
                                cannot reach the project repository)

START
-----
  Linux:    tar xzf Nova3-Linux-x64.tar.gz && ./START.sh
  macOS:    unzip Nova3-Mac-*.zip && double-click START.command
  Windows:  unzip Nova3-Windows.zip && double-click START.cmd
  Source:   tar xzf nova3-source.tar.gz && ./install.sh
            (Windows: unzip, then double-click install.cmd)

The first run installs everything for your user only (a private Node.js if
needed, the pinned DeepSeek Harness runtime, the worker profile and a
"nova3-dsh" command) and then asks for YOUR OWN API key:

  * the key is hidden while you type it, used for one verification request,
    and is NOT written to any file by the installer;
  * it is read from an environment variable (DEEPSEEK_API_KEY, or
    OPENROUTER_API_KEY) that you place in your own shell profile;
  * everything is billed to your own account.

WHAT IT DOES AFTERWARDS
-----------------------
  nova3-dsh            continue the work (run it again whenever you like)
  nova3-dsh status     configuration, key fingerprint, recent passes
  nova3-dsh doctor     tell you exactly what is missing
  nova3-dsh stop       stop after the current pass
  nova3-dsh help       all commands

Progress: the checkout's docs/progress/ files. Logs: .local/dsh-loop/.

RULES OF THE PROJECT: see AGENTS.md in the source download. The worker may not
redistribute original game files, may not patch or ship original binaries, and
must prove its work with tests and recorded evidence.
