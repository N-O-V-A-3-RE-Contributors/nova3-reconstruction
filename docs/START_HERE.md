# Start here

This is the ten-minute path from "I have a computer" to "a worker is helping". If you would rather read the architecture first, see [CONTROL_PLANE](CONTROL_PLANE.md) and [WORKER_LOOP](WORKER_LOOP.md).

## What do you want to do?

| Goal | Go to |
| --- | --- |
| Donate my computer's spare time to the project | [Run a worker](#run-a-worker) |
| Write or fix C++/Java myself | [Work locally](#work-locally) |
| Administer the queue and the AI | [Owner commands](#owner-commands) |

## Run a worker

You need: a 64-bit Linux, macOS (Intel or Apple silicon) or Windows 10/11 machine; a GitHub account with **read** access to this repository; your own DeepSeek or OpenRouter API key.

1. **Get access.** Ask an owner for a read invitation and accept it.
2. **Download.** Open [Releases](https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction/releases) and take the archive for your platform. If you cannot use Git, the `nova3-source.tar.gz` asset works the same way.
3. **Unpack and start.**
* Linux: `tar xzf Nova3-Linux-x64.tar.gz`, then `./START.sh`
* macOS: unzip, then double-click `START.command`
* Windows: unzip, then double-click `START.cmd`
4. **Answer the wizard.** It checks the machine, installs the worker under your own user account, signs in with your GitHub account, and asks for your AI key. The key is used from your shell/profile, never uploaded.
5. **Leave it running.** It claims one scoped task at a time and opens a pull request when finished.

Useful commands afterwards:

```bash
nova3-dsh            # start / resume the worker
nova3-dsh status     # queue, claim, current task
nova3-dsh logs       # recent activity
nova3-dsh doctor     # repair a broken install
```

Stop with `Ctrl+C`. Nothing runs while the machine is off; claims expire and are returned to the queue. Your provider usage is your own.

## Work locally

```bash
git clone https://github.com/N-O-V-A-3-RE-Contributors/nova3-reconstruction.git
cd nova3-reconstruction
cmake -S . -B build/host -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build/host
ctest --test-dir build/host --output-on-failure
```

For analysis of the original binaries, run `./nova3 doctor` first; see [DECOMPILE](DECOMPILE.md). Reference game files are never included and never uploaded.

## Owner commands

```bash
python -m nova3_team.dsh_status              # phone-readable worker status
python -m nova3_team.analysis                # inventory, coverage, ETA
python -m nova3_team.review --repo … --pr N  # compliance verdict for a PR
python -m tools.dsh_loop.toolchain --tier project --plan
```

Inside DeepSeek Harness, the `/nova` plugin exposes the same controls: `/nova status`, `/nova eta`, `/nova review <pr>`, `/nova fleet`, `/nova toolchain`. Details, roles and the release process: [CONTROL_PLANE](CONTROL_PLANE.md).

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| `Cannot access repository` | Accept the GitHub invitation; ask for read access. |
| Provider key rejected | Re-run the wizard or re-save it in the DSH Models page. |
| No tasks | The queue is empty or paused for the owner's decision. Wait; the worker polls. |
| Task blocked | The issue is quarantined for a maintainer; the checkout is preserved. |
| Installer wants a tool | Re-run with network access and accept the package install, or install it with your OS package manager. |

Do not disable security features or install software from an unfamiliar source. If a warning looks suspicious, ask an owner before continuing.
