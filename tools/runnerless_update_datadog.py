#!/usr/bin/env python3
"""Resolve a coordinated SDK candidate in the native producer's private checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler

from materialize_shared_app import APP, materialize, shared_files

ROOT = Path(__file__).resolve().parents[1]
SDK_LOCK = Path('bazel/datadog_sdk_versions.json')
PROFILE_LOCK = Path('corpus/datadog/sdk_profiles.json')
OCI_LOCK = Path('bazel/oci_images.lock.bzl')
RUBY_IMAGE = 'docker.io/library/ruby:3.3.12-slim-bookworm@sha256:df209d82bb55aeb26b9b08922ee22c15f19b3458455690132dc07a5c52dc1c99'
GO_IMAGE = 'docker.io/library/golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437'
BASELINES = {'ruby': '2.43.0', 'python': '4.15.5', 'go': '2.10.1'}
VERSION = r'[0-9]+\.[0-9]+\.[0-9]+'
DIGEST = r'sha256:[0-9a-f]{64}'
TRACER_MODULE = r'github.com/DataDog/dd-trace-go/(?:v2|contrib/[A-Za-z0-9._/-]+/v2)'


def document(path):
    if path.is_symlink() or path.stat().st_size > 512 * 1024:
        raise ValueError('bounded regular document required')
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def run(arguments, *, root=ROOT, data=None, capture=False, limit=2*1024*1024, timeout=3600):
    # Native orchestration already supplies a clean, private checkout and clears
    # inherited Git overrides. No command receives registry secrets as arguments.
    environment=dict(os.environ)
    if Path(str(arguments[0])) != ROOT/'tools/publish_datadog_fixtures.sh':
        environment.pop('DATADOG_REGISTRY_TOKEN',None)
    environment.pop('GITHUB_TOKEN',None);environment.pop('GH_TOKEN',None)
    process=subprocess.Popen([str(a) for a in arguments],cwd=root,env=environment,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+timeout
        result=bytearray()
        with selectors.DefaultSelector() as streams:
            pending=memoryview(data or b'')
            if data is not None:
                if pending:streams.register(process.stdin,selectors.EVENT_WRITE)
                else:process.stdin.close()
            if capture:streams.register(process.stdout,selectors.EVENT_READ)
            while streams.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(arguments,timeout)
                for key,_ in streams.select(min(remaining,1)):
                    if key.fileobj is process.stdin:
                        pending=pending[os.write(key.fd,pending[:4096]):]
                        if not pending:
                            streams.unregister(key.fileobj);key.fileobj.close()
                    else:
                        chunk=os.read(key.fd,min(65536,limit-len(result)+1))
                        if not chunk:
                            streams.unregister(key.fileobj);key.fileobj.close()
                        result.extend(chunk)
                        if len(result)>limit:raise ValueError('command output bound')
        if process.wait(timeout=max(0,deadline-time.monotonic())):
            raise ValueError('candidate command failed')
        return bytes(result)
    finally:
        if process.poll() is None:
            process.kill();process.wait()
        for pipe in (process.stdin,process.stdout):
            if pipe is not None:pipe.close()


def current_versions(root):
    result=document(root/SDK_LOCK)
    if set(result)!=set(BASELINES) or any(not isinstance(v,str) or not re.fullmatch(VERSION,v) for v in result.values()):
        raise ValueError('invalid current SDK versions')
    return result


def targets(releases, old):
    if set(releases)!=set(BASELINES):
        raise ValueError('release set must contain all SDKs')
    result={}
    for language in BASELINES:
        item=releases[language]
        if not isinstance(item,dict) or set(item)!={'value','url','sha256'} or not re.fullmatch('[0-9a-f]{64}',item.get('sha256','')):
            raise ValueError('invalid native registry observation')
        value=item['value']
        if not isinstance(value,str) or not re.fullmatch('v?'+VERSION,value):
            raise ValueError('only stable SDK releases are supported')
        value=value.removeprefix('v')
        if tuple(map(int,value.split('.')))<tuple(map(int,old[language].split('.'))):
            raise ValueError('registry would downgrade an SDK')
        result[language]=value
    return result


def replace_once(path, pattern, replacement):
    text,count=re.subn(pattern,replacement,path.read_text(),flags=re.M)
    if count!=1:
        raise ValueError('unexpected source layout')
    path.write_text(text)


def image_pin(text, name):
    marker=r'^DATADOG_'+name.upper()+r' = struct\(\n' if name in ('ruby','python') else r'^    "gin_datadog_realworld": struct\(\n'
    matches=re.findall(marker+r'.*?^\s*\),?$',text,re.M|re.S)
    if len(matches)!=1:
        raise ValueError('unexpected image pin')
    values=re.findall(r'^\s+digest = "('+DIGEST+r')",$',matches[0],re.M)
    if len(values)!=1:
        raise ValueError('missing immutable image pin')
    return values[0]


def python_pin(root, version):
    # This is the official injection-package tag convention. Existence and the
    # SDK inside it are independently checked, never inferred from the tag alone.
    tag=version+'-1'
    raw=run(['skopeo','inspect','--no-creds','--raw','docker://install.datadoghq.com/apm-library-python-package:'+tag],capture=True)
    value=json.loads(raw)
    if value.get('schemaVersion')!=2 or not isinstance(value.get('manifests'),list):
        raise ValueError('official Python image must be an immutable index')
    matches=[m for m in value['manifests'] if m.get('platform')=={'os':'linux','architecture':'amd64'}]
    if len(matches)!=1 or not re.fullmatch(DIGEST,matches[0].get('digest','')):
        raise ValueError('official Python image lacks unique linux/amd64')
    path=root/OCI_LOCK
    text=path.read_text();start=text.index('DATADOG_PYTHON = struct(');end=text.index('\n)',start)+2
    block=text[start:end]
    block,count=re.subn(r'(?m)^(\s+digest = ")[^"]+("),$',lambda m:m[1]+'sha256:'+hashlib.sha256(raw).hexdigest()+m[2]+',',block)
    if count!=1:raise ValueError('Python digest pin layout')
    block,count=re.subn(r'(?m)^(\s+version = ")[^"]+("),$',lambda m:m[1]+tag+m[2]+',',block)
    if count!=1:raise ValueError('Python version pin layout')
    path.write_text(text[:start]+block+text[end:])


def ruby_graph(root, version, scratch):
    context=root/'fixtures/agents/datadog-ruby'
    replace_once(context/'Gemfile',r'^(gem "datadog", ")[^"]+("$)',lambda m:m[1]+version+m[2])
    # Resolve the real Bundler graph with the exact interpreter and Bundler used
    # by the payload builder, rather than rewriting transitive lock text.
    run([os.environ.get('CONTAINER_TOOL','podman'),'run','--rm','--network=host','-v',str(context)+':/work:Z',
        '--workdir','/work','--entrypoint','sh',RUBY_IMAGE,'-ceu',
        'gem install bundler -v 4.0.3 --no-document >/dev/null; bundle _4.0.3_ lock --update datadog'])
    run([os.environ.get('CONTAINER_TOOL','podman'),'run','--rm','-i','--network=host','-v',str(root/'bazel')+':/work:Z',
        '--entrypoint','ruby',RUBY_IMAGE,'-',version,'/work/datadog_ruby.lock.json'],
        data=(root/'tools/resolve_datadog_ruby_matrix.rb').read_bytes())
    for filename in ['activation.rb','matrix_activation.rb','matrix_build.rb']:
        path=context/filename;text=path.read_text()
        # Only current fixture bootstraps change; historical profiles never do.
        old=current_versions(root)['ruby']
        if old not in text:raise ValueError('missing Ruby bootstrap pin')
        path.write_text(text.replace(old,version))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def module_minimum(module,version):
    # Go proxy escaping is defined by the module protocol; these module names
    # are fixed public DataDog dependencies, never caller-selected origins.
    escaped=''.join('!'+letter.lower() if letter.isupper() else letter for letter in module)
    url='https://proxy.golang.org/'+escaped+'/@v/v'+version+'.mod'
    with build_opener(NoRedirect()).open(Request(url),timeout=30) as response:
        if response.status!=200:raise ValueError('module observation unavailable')
        raw=response.read(128*1024+1)
    if len(raw)>128*1024:raise ValueError('module metadata bound')
    matches=re.findall(r'^go 1\.([0-9]+)(?:\.[0-9]+)?$',raw.decode(),re.M)
    if len(matches)!=1:raise ValueError('module Go minimum unknown')
    return int(matches[0])


def go_graph(root, version, scratch):
    minimum=module_minimum('github.com/DataDog/dd-trace-go/v2',version)
    builders=document(root/'bazel/datadog_go_builders.json')
    image=builders.get(str(minimum))
    if not isinstance(image,str) or not re.fullmatch(r'docker.io/library/golang:1\.[0-9]+-bookworm@'+DIGEST,image):
        raise ValueError('review a pinned compiler for the SDK Go minimum')
    replace_once(root/APP/'Dockerfile',r'^FROM docker.io/library/golang:[^ ]+ AS builder$', 'FROM '+image+' AS builder')
    full=scratch/'gin'
    materialize(shared_files(root),root/APP,full)
    for name in ['go.mod','go.sum','orchestrion.tool.go']:
        shutil.copy2(full/'datadog'/name,full/name)
    shutil.copy2(full/'datadog/probes.go',full/'probes_datadog.go')
    for directory,destination in [(full,root/APP/'datadog'),(root/'harness/shared_sdk/go',root/'harness/shared_sdk/go'),
            (root/'harness/go_runtime/orchestrion',root/'harness/go_runtime/orchestrion')]:
        working=scratch/('gin-resolve' if directory==full else 'resolve-'+directory.name)
        shutil.copytree(directory,working,symlinks=True)
        modules=sorted(set(re.findall(r'('+TRACER_MODULE+r')\s+v'+VERSION,(working/'go.mod').read_text())))
        if not modules:raise ValueError('missing current tracer graph')
        run([os.environ.get('CONTAINER_TOOL','podman'),'run','--rm','--network=host','-v',str(working)+':/work:Z',
            '--workdir','/work','--env','GOTOOLCHAIN=local','--env','GOPROXY=https://proxy.golang.org',
            '--env','GOSUMDB=sum.golang.org','--entrypoint','sh',image,'-ceu',
            'go get "$@"; go mod tidy; go mod verify','--',*[module+'@v'+version for module in modules]])
        declared=re.findall(r'('+TRACER_MODULE+r')\s+(v'+VERSION+r')',(working/'go.mod').read_text())
        if not declared or any(v!='v'+version for _,v in declared):raise ValueError('Go tracer graph not coordinated')
        for name in ['go.mod','go.sum']:shutil.copy2(working/name,destination/name)
    runtime_lock=root/'harness/go_runtime/versions.lock.json'
    lock=document(runtime_lock)
    tool=re.findall(r'github.com/DataDog/orchestrion\s+v('+VERSION+r')',(root/'harness/go_runtime/orchestrion/go.mod').read_text())
    if len(tool)!=1:raise ValueError('runtime Orchestrion identity unknown')
    tool_minimum=module_minimum('github.com/DataDog/orchestrion',tool[0])
    pin=lock['instrumentation']['orchestrion']
    pin.update(version=tool[0],sdkVersion=version,minimumGoMinor=max(minimum,tool_minimum),
        minimumSource=('https://github.com/DataDog/dd-trace-go/blob/v'+version+'/go.mod' if minimum>=tool_minimum
            else 'https://github.com/DataDog/orchestrion/blob/v'+tool[0]+'/go.mod'))
    write_json(runtime_lock,lock)


def profiles(root, old, new):
    catalog=document(root/PROFILE_LOCK)
    if not isinstance(catalog,list):raise ValueError('profile catalog must be a list')
    existing={item['id'] for item in catalog}
    for language in BASELINES:
        if old[language]==new[language]:continue
        baseline=BASELINES[language];before='v'+baseline.replace('.','-');after='v'+new[language].replace('.','-')
        implementation=language+'-v'+new[language]
        source=root/'corpus/datadog/implementation'/f'{language}-v{baseline}.scm'
        text=source.read_text();old_digest=image_pin((root/OCI_LOCK).read_text(), 'gin' if language=='go' else language)
        text=text.replace(baseline,new[language])
        if language=='go':
            selected=re.findall(r'github.com/DataDog/orchestrion\s+v('+VERSION+r')',(root/APP/'datadog/go.mod').read_text())
            if len(selected)!=1:raise ValueError('missing resolved Orchestrion version')
            text,count=re.subn(r'orchestrion-'+VERSION,'orchestrion-'+selected[0],text)
            if count!=1:raise ValueError('Orchestrion implementation identity')
        text,count=re.subn(DIGEST,old_digest,text)
        if count!=1:raise ValueError('implementation image identity')
        destination=source.with_name(implementation+'.scm')
        if destination.exists() and destination.read_text()!=text:raise ValueError('existing versioned implementation differs')
        destination.write_text(text)
        for source in sorted((root/'corpus/datadog/realworld/profile').glob(language+'-*-'+before+'-*.scm')):
            profile=source.stem.replace(before,after)
            text=source.read_text().replace(before,after).replace(baseline,new[language])
            destination=source.with_name(profile+'.scm')
            if destination.exists() and destination.read_text()!=text:raise ValueError('existing versioned profile differs')
            destination.write_text(text)
            reviewed='sinatra' not in profile
            item={'id':profile,'implementation':implementation,'wire':'v0.5' if profile.endswith('-v05') else 'v0.4','reviewed':reviewed}
            if reviewed:item['reference']=source.stem
            if profile not in existing:catalog.append(item);existing.add(profile)
    write_json(root/PROFILE_LOCK,sorted(catalog,key=lambda item:item['id']))


def changed_paths(root,base):
    raw=run(['git','diff','--name-only','-z',base],root=root,capture=True)
    paths=[p for p in raw.decode().split('\0') if p]
    if len(paths)>64 or any(not re.fullmatch(r'[A-Za-z0-9_./-]+',p) or '..' in p.split('/') for p in paths):
        raise ValueError('candidate paths exceed the native contract')
    return paths


def main():
    old=current_versions(ROOT)
    target=targets(document(Path(os.environ['RUNNERLESS_DEPENDENCY_RELEASES_FILE'])),old)
    base=os.environ['RUNNERLESS_DEPENDENCY_BASE_SHA'];receipt=Path(os.environ['RUNNERLESS_DEPENDENCY_UPDATES_FILE'])
    if (os.environ.get('RUNNERLESS_DEPENDENCY_REPOSITORY_ID')!='1402998689'
            or os.environ.get('RUNNERLESS_DEPENDENCY_GROUP')!='datadog-sdks'
            or not re.fullmatch('[0-9a-f]{40}',base)
            or run(['git','rev-parse','HEAD'],capture=True).decode().strip()!=base
            or run(['git','status','--porcelain','--untracked-files=all'],capture=True)):
        raise ValueError('updater requires its authorized fresh checkout')
    if old==target:
        write_json(receipt,[]);return
    initial_oci=(ROOT/OCI_LOCK).read_text()
    with tempfile.TemporaryDirectory(prefix='datadog-sdk-update-') as temporary:
        scratch=Path(temporary)
        if old['ruby']!=target['ruby']:ruby_graph(ROOT,target['ruby'],scratch)
        write_json(ROOT/SDK_LOCK,target)
        if old['go']!=target['go']:go_graph(ROOT,target['go'],scratch)
        if old['python']!=target['python']:python_pin(ROOT,target['python'])
        run([sys.executable,ROOT/'tools/update_shared_sdk_cases.py'])
        # Resolve fetched module metadata before the publisher's immutable-source
        # checks. The native runner explicitly permits this source-only commit.
        run(['bazel','cquery','--config=local','@rules_stests//fixtures/apps/go/realworld-gin:sources','--output=files'])
        run(['git','add','--all'])
        run(['git','-c','commit.gpgsign=false','commit','--no-verify','-m','Prepare coordinated Datadog SDK sources'])
        images=scratch/'images'
        run([ROOT/'tools/build_datadog_fixtures.sh',images,'--candidate'])
        run([ROOT/'tools/publish_datadog_fixtures.sh',images,'--candidate'])
        profiles(ROOT,old,target)
        verification_targets=['//corpus:datadog_conformance_test',
            '//harness:datadog_sink_test','//fixtures:datadog_suite',
            '//fixtures:datadog_shared_sdk_go_0_test','//fixtures:datadog_shared_sdk_python_0_test',
            '//tools:runnerless_update_datadog_test']
        run(['bazel','cquery','--config=local','set('+' '.join(verification_targets)+')','--output=label'])
        run(['git','add','--all'])
        paths=changed_paths(ROOT,base)
        urls={language:f'https://github.com/DataDog/dd-trace-{suffix}/releases/tag/v{target[language]}'
            for language,suffix in [('ruby','rb'),('python','py'),('go','go')]}
        updates=[{'ecosystem':language,'name':{'ruby':'datadog','python':'ddtrace','go':'github.com/DataDog/dd-trace-go/v2'}[language],
            'from':('v' if language=='go' else '')+old[language],'to':('v' if language=='go' else '')+target[language],
            'releaseUrl':urls[language],'paths':paths} for language in BASELINES if old[language]!=target[language]]
        final_oci=(ROOT/OCI_LOCK).read_text()
        for fixture,repository,language in [('ruby','ghcr.io/pawelchcki/rules_stest_agents','ruby'),
            ('gin','ghcr.io/pawelchcki/rules_stest_apps','go'),('python','install.datadoghq.com/apm-library-python-package','python')]:
            before=image_pin(initial_oci,fixture);after=image_pin(final_oci,fixture)
            if before!=after:updates.append({'ecosystem':'oci','name':repository,'from':before,'to':after,'releaseUrl':urls[language],'paths':paths})
        write_json(receipt,updates)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        sys.exit('Coordinated Datadog update failed; candidate not accepted')
