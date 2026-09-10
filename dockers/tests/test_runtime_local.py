import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('runtime', Path(__file__).parents[1] / 'runtime-local.py')
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class StaticSafetyTest(unittest.TestCase):
    def test_build_context_contains_only_public_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            paths = ['index.html', '2026/es/index.html', '2026/img/logo.svg', '2026/.env', '2026/private.py',
                     '.git-secret', 'ai-harness-local/state.html', 'dockers/private.html',
                     '17 MVD/report public.pdf', '17 MVD/server.py', 'AGENTS.md', 'CNAME',
                     'dockers/Dockerfile.local', 'dockers/nginx.conf']
            for name in paths:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(name)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            (root / '2026/untracked.html').write_text('untracked private file')
            output = io.BytesIO()
            RUNTIME.write_context(output, root)
            output.seek(0)
            with tarfile.open(fileobj=output) as bundle:
                self.assertEqual(set(bundle.getnames()), {'site/index.html', 'site/2026/es/index.html',
                    'site/2026/img/logo.svg', 'site/17 MVD/report public.pdf', 'Dockerfile', 'nginx.conf'})
                self.assertTrue(all(member.isfile() for member in bundle.getmembers()))

    def test_tracked_public_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'index.html').write_text('site')
            (root / '2026').mkdir()
            (root / '2026/secret.html').symlink_to('/etc/passwd')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            with self.assertRaises(RuntimeError):
                RUNTIME.public_files(root)

    def test_foreign_alias_under_compose_labels_blocks_mutation(self):
        result = subprocess.CompletedProcess([], 0, stdout=b'foreign-id\n')
        labels = {'com.docker.compose.project': RUNTIME.PROJECT, 'ai.harness.project-root': '/foreign',
                  'com.docker.compose.service': 'app'}
        with patch.object(RUNTIME, 'run', return_value=result), \
                patch.object(RUNTIME, 'inspect', return_value={'Config': {'Labels': labels}}), \
                self.assertRaises(RuntimeError):
            RUNTIME.assert_ownership()

    def test_git_add_and_remove_from_index_invalidate_public_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'index.html').write_text('site')
            (root / '2026').mkdir()
            candidate = root / '2026/new.html'
            candidate.write_text('public candidate')
            subprocess.run(['git', '-C', str(root), 'add', 'index.html'], check=True)
            first = RUNTIME.public_digest(root)
            image = {'Config': {'Labels': {'ai.harness.public-content': first}}}
            self.assertEqual(RUNTIME.reconcile_public_image({'action': 'reuse'}, image, first)['action'], 'reuse')
            subprocess.run(['git', '-C', str(root), 'add', '2026/new.html'], check=True)
            added = RUNTIME.public_digest(root)
            self.assertNotEqual(first, added)
            self.assertEqual(RUNTIME.reconcile_public_image({'action': 'reuse'}, image, added)['action'], 'fresh')
            image['Config']['Labels']['ai.harness.public-content'] = added
            subprocess.run(['git', '-C', str(root), 'rm', '-q', '--cached', '2026/new.html'], check=True)
            self.assertTrue(candidate.is_file())
            removed = RUNTIME.public_digest(root)
            self.assertEqual(first, removed)
            self.assertEqual(RUNTIME.reconcile_public_image({'action': 'reuse'}, image, removed)['action'], 'fresh')

    def test_failed_readiness_cannot_mark_ready(self):
        calls = []
        def harness(action, *args):
            calls.append(action)
            return {'action': 'reuse'} if action == 'decide' else None
        with patch.object(RUNTIME, 'validate'), patch.object(RUNTIME, 'run'), \
                patch.object(RUNTIME, 'assert_ownership'), patch.object(RUNTIME, 'public_digest', return_value='x'), \
                patch.object(RUNTIME, 'inspect', return_value={'Config': {'Labels': {'ai.harness.public-content': 'x'}}}), \
                patch.object(RUNTIME, 'harness', side_effect=harness), patch.object(RUNTIME, 'compose'), \
                patch.object(RUNTIME, 'check_site', side_effect=RuntimeError('private path exposed')), \
                patch.object(RUNTIME.time, 'monotonic', side_effect=[0, 100]), self.assertRaises(RuntimeError):
            RUNTIME.up()
        self.assertNotIn('mark-ready', calls)


if __name__ == '__main__':
    unittest.main()
