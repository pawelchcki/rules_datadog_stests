"""Coordinated generation preserves reviewed history and refuses false pins."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import runnerless_update_datadog as updater

SOURCE=Path(__file__).resolve().parents[1]


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        # The generator's immutable baseline fixtures remain meaningful after
        # this repository advances its active SDKs or adds historical profiles.
        for language,version in updater.BASELINES.items():
            implementation=Path('corpus/datadog/implementation')/(language+'-v'+version+'.scm')
            (self.root/implementation).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(SOURCE/implementation,self.root/implementation)
            pattern=language+'-*-v'+version.replace('.','-')+'-*.scm'
            for source in (SOURCE/'corpus/datadog/realworld/profile').glob(pattern):
                target=self.root/'corpus/datadog/realworld/profile'/source.name
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        for relative in [updater.OCI_LOCK,updater.SDK_LOCK,updater.PROFILE_LOCK]:
            (self.root/relative).parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(SOURCE/relative,self.root/relative)
        (self.root/updater.APP/'datadog').mkdir(parents=True)
        (self.root/updater.APP/'datadog/go.mod').write_text('require github.com/DataDog/orchestrion v1.14.0\n')
        updater.write_json(self.root/updater.SDK_LOCK,updater.BASELINES)
        updater.write_json(self.root/updater.PROFILE_LOCK,[])
        self.old=updater.current_versions(self.root)
        self.new={'ruby':'2.47.0','python':'4.16.1','go':'2.11.2'}

    def releases(self):
        return {language:{'value':('v' if language!='ruby' else '')+value,
            'url':'https://example.invalid/releases','sha256':'a'*64} for language,value in self.new.items()}

    def test_command_bounds_cover_blocked_input_and_output_before_process_exit(self):
        self.assertEqual(updater.run([sys.executable,'-c',
            'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'],
            root=self.root,data=b'bounded input',capture=True),b'bounded input')
        with self.assertRaises(ValueError):
            updater.run([sys.executable,'-c','print("x"*1000)'],root=self.root,capture=True,limit=100)
        for capture,data in [(True,None),(False,b'x'*(1024*1024))]:
            with self.assertRaises(subprocess.TimeoutExpired):
                updater.run([sys.executable,'-c','import time;time.sleep(30)'],
                    root=self.root,capture=capture,data=data,timeout=0.2)

    def test_failed_candidate_build_never_reaches_registry_or_emits_a_receipt(self):
        release=self.root/'releases.json';release.write_text(json.dumps(self.releases()))
        receipt=self.root/'updates.json';base='a'*40;calls=[]
        def execute(arguments,**options):
            argv=[str(a) for a in arguments];calls.append(argv)
            if argv[:3]==['git','rev-parse','HEAD']:return (base+'\n').encode()
            if argv[:3]==['git','status','--porcelain']:return b''
            if argv[0].endswith('build_datadog_fixtures.sh'):raise ValueError('build failed')
            return b''
        with patch.object(updater,'ROOT',self.root),patch.object(updater,'run',side_effect=execute), \
                patch.object(updater,'ruby_graph'),patch.object(updater,'go_graph'),patch.object(updater,'python_pin'), \
                patch.dict(os.environ,{'RUNNERLESS_DEPENDENCY_RELEASES_FILE':str(release),
                    'RUNNERLESS_DEPENDENCY_UPDATES_FILE':str(receipt),'RUNNERLESS_DEPENDENCY_BASE_SHA':base,
                    'RUNNERLESS_DEPENDENCY_REPOSITORY_ID':'1402998689','RUNNERLESS_DEPENDENCY_GROUP':'datadog-sdks'}):
            with self.assertRaises(ValueError):updater.main()
        self.assertFalse(receipt.exists())
        self.assertFalse(any(call[0].endswith('publish_datadog_fixtures.sh') for call in calls))
        source_commit=next(i for i,call in enumerate(calls) if call[:2]==['git','-c'])
        build=next(i for i,call in enumerate(calls) if call[0].endswith('build_datadog_fixtures.sh'))
        self.assertLess(source_commit,build)

    def test_stable_native_registry_observations_cannot_downgrade_or_invent_prereleases(self):
        self.assertEqual(updater.targets(self.releases(),self.old),self.new)
        for fault in ['downgrade','prerelease','missing','invalid-hash','non-string']:
            releases=self.releases()
            if fault=='downgrade':releases['go']['value']='v2.0.0'
            if fault=='prerelease':releases['python']['value']='v4.16.1rc1'
            if fault=='missing':del releases['ruby']
            if fault=='invalid-hash':releases['ruby']['sha256']='latest'
            if fault=='non-string':releases['ruby']['value']=1
            with self.assertRaises(ValueError,msg=fault):updater.targets(releases,self.old)

    def test_new_profile_generation_keeps_every_historical_byte_and_exact_reference_expectations(self):
        historical={path.relative_to(self.root):path.read_bytes() for path in self.root.rglob('*.scm')}
        updater.profiles(self.root,self.old,self.new)
        for relative,data in historical.items():self.assertEqual((self.root/relative).read_bytes(),data)
        catalog=updater.document(self.root/updater.PROFILE_LOCK)
        self.assertEqual(len(catalog),16)
        self.assertIn('orchestrion-1.14.0',(self.root/'corpus/datadog/implementation/go-v2.11.2.scm').read_text())
        self.assertEqual(sum(item['reviewed'] for item in catalog),7)
        for item in catalog:
            spec=(self.root/'corpus/datadog/realworld/profile'/(item['id']+'.scm')).read_text()
            self.assertIn("(id '"+item['id']+')',spec)
            if item['reviewed']:
                self.assertIn(Path('corpus/datadog/realworld/profile')/(item['reference']+'.scm'),historical)
            else:self.assertNotIn('reference',item)
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        updater.profiles(self.root,self.old,self.new)
        self.assertEqual({p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()},before)

    def test_an_existing_versioned_profile_is_never_silently_overwritten(self):
        updater.profiles(self.root,self.old,self.new)
        path=self.root/'corpus/datadog/implementation/ruby-v2.47.0.scm'
        path.write_text('reviewed unrelated implementation')
        with self.assertRaises(ValueError):updater.profiles(self.root,self.old,self.new)
        self.assertEqual(path.read_text(),'reviewed unrelated implementation')

    def test_single_language_upgrade_adds_only_its_new_profiles(self):
        new=dict(self.old,ruby='2.47.0')
        updater.profiles(self.root,self.old,new)
        catalog=updater.document(self.root/updater.PROFILE_LOCK)
        self.assertEqual(len(catalog),11)
        self.assertTrue(all(item['id'].startswith('ruby-') for item in catalog))

    def test_python_pin_uses_actual_anonymous_index_bytes_and_keeps_other_images(self):
        raw=json.dumps({'schemaVersion':2,'manifests':[{'platform':{'os':'linux','architecture':'amd64'},'digest':'sha256:'+'1'*64}]}).encode()
        original=(self.root/updater.OCI_LOCK).read_text()
        with patch.object(updater,'run',return_value=raw) as run:
            updater.python_pin(self.root,'4.16.1')
            self.assertIn('--no-creds',run.call_args.args[0])
        result=(self.root/updater.OCI_LOCK).read_text()
        self.assertEqual(updater.image_pin(result,'python'),'sha256:'+hashlib.sha256(raw).hexdigest())
        self.assertEqual(updater.image_pin(result,'ruby'),updater.image_pin(original,'ruby'))
        self.assertIn('version = "4.16.1-1"',result)
        for value in [{'schemaVersion':2,'manifests':[]},{'schemaVersion':1,'manifests':[]},
            {'schemaVersion':2,'manifests':[{'platform':{'os':'linux','architecture':'amd64'},'digest':'latest'}]}]:
            with patch.object(updater,'run',return_value=json.dumps(value).encode()):
                with self.assertRaises(ValueError):updater.python_pin(self.root,'4.16.1')
            self.assertEqual((self.root/updater.OCI_LOCK).read_text(),result)


if __name__=='__main__':unittest.main()
