"""公开包只包含白名单源码及明确的公开验证资料，不包含私人历史。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ReleasePackageTests(unittest.TestCase):
    def test_payload_excludes_runtime_private_reports_and_embedded_git(self):
        spec=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/build_release.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source'
            root.mkdir()
            for name in ('aireos/core.py','tests/test_core.py','README.md','pyproject.toml',
                'verification/reviews/private.md','.aireos/owner.key','aireos/__pycache__/secret.pyc',
                'docs/.private.md','docs/link.md','legacy/kit/private.txt'):
                path=root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('synthetic',encoding='utf-8')
            output=Path(temporary)/'release'
            manifest=module.build(root,output,'v2.0.0-alpha.1')
            self.assertEqual({'aireos/core.py','tests/test_core.py','README.md','pyproject.toml','docs/link.md'},set(manifest['files']))
            with zipfile.ZipFile(next(output.glob('*.zip'))) as archive:
                names=archive.namelist()
                self.assertFalse(any('owner.key' in name or 'private.md' in name or '__pycache__' in name or 'legacy/' in name for name in names))
                self.assertIn('MANIFEST.json',names)
            with self.assertRaises(FileExistsError):
                module.build(root,output,'v2.0.0-alpha.1')

    def test_verification_timeout_keeps_partial_log_and_failed_report(self):
        import contextlib
        import io
        import subprocess
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('release_verifier',ROOT/'scripts/verify.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for partial in (b'last test still running', 'last test still running'):
            with self.subTest(partial_type=type(partial).__name__), tempfile.TemporaryDirectory() as temporary:
                failure=subprocess.TimeoutExpired(['synthetic'],120,output=partial,stderr=partial)
                original_run=module.subprocess.run
                def run(command,*args,**kwargs):
                    if isinstance(command,list) and 'unittest' in command:
                        raise failure
                    return original_run(command,*args,**kwargs)
                with patch.object(module.subprocess,'run',side_effect=run), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    status=module.main(['--output',temporary])
                self.assertEqual(1,status)
                report=json.loads(next(Path(temporary).glob('result-*.json')).read_text(encoding='utf-8'))
                self.assertFalse(report['passed'])
                self.assertTrue(report['timed_out'])
                self.assertEqual(124,report['returncode'])
                self.assertIn('last test still running',(Path(temporary)/report['log']).read_text(encoding='utf-8'))

    def test_explicit_root_files_must_not_follow_links(self):
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/build_release.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source'
            root.mkdir()
            (root/'README.md').write_text('must not export linked bytes',encoding='utf-8')
            original=Path.is_symlink
            with patch.object(Path,'is_symlink',lambda path:path.name=='README.md' or original(path)):
                manifest=module.build(root,Path(temporary)/'release','v2.0.0-alpha.1')
            self.assertNotIn('README.md',manifest['files'])


if __name__=='__main__':
    unittest.main()
