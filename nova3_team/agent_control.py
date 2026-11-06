"""Human /nova controls: stop/resume execution AND the approval loop.

Uses the supervisor's existing STOP/RESUME protocol, never kills a process or
clears checkpoints. Enabling first runs autopilot's owner/admin authorization;
a failed authorization leaves STOP intact. No model calls.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def set_execution(control: Path, enabled: bool) -> None:
    control.mkdir(parents=True, exist_ok=True, mode=0o700)
    if enabled:
        (control / 'STOP').unlink(missing_ok=True)
        (control / 'RESUME').write_text('Explicit /nova on request\n')
    else:
        # Write stop before disabling approvals: even if GitHub is unavailable,
        # no new paid session should start after the next supervisor check.
        (control / 'STOP').write_text('Paused by /nova off\n')
        (control / 'RESUME').unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('on', 'off'))
    parser.add_argument('--repo', required=True)
    parser.add_argument('--slot', default='dsh')
    args = parser.parse_args(argv)
    from . import worker
    control = Path(worker.slot_paths(worker.state_root(args.repo), args.slot).state) / 'control'
    if args.action == 'off':
        set_execution(control, False)
    result = subprocess.run([sys.executable, '-m', 'nova3_team.autopilot',
                             'enable' if args.action == 'on' else 'off',
                             '--repo', args.repo], check=False)
    if result.returncode:
        return result.returncode
    if args.action == 'on':
        set_execution(control, True)
    print('Execution and autopilot ' + args.action +
          ('; supervisor resumes on its next control check.' if args.action == 'on'
           else '; active work stops at the supervisor cancellation check.'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
