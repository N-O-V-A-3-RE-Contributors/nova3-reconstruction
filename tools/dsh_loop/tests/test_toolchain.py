"""Per-platform project toolchain: what is needed, and how to install it."""
import unittest

from tools.dsh_loop import toolchain


class SpecTests(unittest.TestCase):
    def test_every_tier_has_packages(self):
        for tier in toolchain.TIERS:
            names = [name for name, spec in toolchain.SPECS.items()
                     if tier in spec['tiers']]
            self.assertTrue(names, f'no package declared for tier {tier}')

    def test_commands_are_argv_lists_without_shell_syntax(self):
        for manager in ('pacman', 'apt', 'dnf', 'brew', 'winget'):
            argv = toolchain.install_argv(manager, ['pkg'])
            self.assertIsInstance(argv, list)
            self.assertTrue(all(isinstance(part, str) for part in argv))
            self.assertFalse(any(part in (';', '&&', '|', '>') for part in argv))

    def test_winget_gets_one_id_per_invocation(self):
        plan = toolchain.plan('project', family='windows', manager='winget',
                              present=lambda _name: False)
        for argv in plan['commands']:
            self.assertEqual(argv.count('--id'), 1)
            self.assertEqual(argv[0], 'winget')

    def test_manual_tools_are_named_with_a_url_not_faked(self):
        plan = toolchain.plan('full', family='linux', manager='pacman',
                              present=lambda _name: False)
        names = {entry['name'] for entry in plan['manual']}
        self.assertIn('ghidra', names)
        self.assertIn('ida-pro', names)

    def test_missing_tools_are_reported_with_install_commands(self):
        plan = toolchain.plan('project', family='linux', manager='pacman',
                              present=lambda _name: False)
        self.assertIn('cmake', plan['missing'])
        self.assertTrue(any('cmake' in argv for argv in plan['commands']))

    def test_present_tools_are_not_reinstalled(self):
        plan = toolchain.plan('core', family='linux', manager='pacman',
                              present=lambda name: True)
        self.assertEqual(plan['missing'], [])
        self.assertEqual(plan['commands'], [])


if __name__ == '__main__':
    unittest.main()
