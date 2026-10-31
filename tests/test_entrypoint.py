import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from strix_core import __main__ as cli


class CheckoutStateTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def test_fresh_checkout_uses_private_artifacts_without_the_original_brief(self):
        (self.root/'pyproject.toml').touch()
        (self.root/'packaging').mkdir();(self.root/'packaging/PKGBUILD').touch()
        with patch.object(cli,'__file__',str(self.root/'strix_core/__main__.py')):
            self.assertEqual(cli.default_state(),self.root/'.preview/state')
        self.assertFalse((self.root/'MinimalAC.md').exists())

    def test_installed_package_respects_xdg_state(self):
        state=self.root/'user-state'
        with patch.object(cli,'__file__',str(self.root/'site-packages/strix_core/__main__.py')), \
             patch.dict(os.environ,{'XDG_STATE_HOME':str(state)}):
            self.assertEqual(cli.default_state(),state/'strix-core')

    def test_unrelated_pyproject_does_not_redirect_installed_state(self):
        (self.root/'pyproject.toml').touch()
        state=self.root/'user-state'
        with patch.object(cli,'__file__',str(self.root/'strix_core/__main__.py')), \
             patch.dict(os.environ,{'XDG_STATE_HOME':str(state)}):
            self.assertEqual(cli.default_state(),state/'strix-core')
