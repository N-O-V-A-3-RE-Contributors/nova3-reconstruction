"""NOVA3 DeepSeek Harness (DSH) loop: installer and unattended worker loop.

Public entry points live in ``cli.py``:

* ``python -m tools.dsh_loop install`` -- idempotent per-user setup (Node, DSH
  runtime, worker profile, launcher, optional GitHub CLI).
* ``python -m tools.dsh_loop run`` -- one bounded worker pass.
* ``python -m tools.dsh_loop loop`` -- keep running passes until the budget or a
  stop condition; this is what the generated launchers call.
* ``python -m tools.dsh_loop status`` -- what the current installation thinks.

The package is deliberately dependency-free (standard library only) so it can
run under a bare ``python3`` on a fresh contributor machine.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
