"""Real MariaDB server from digest-pinned OCI rootfs; no host database dependency."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from harness.datadog_agent.probe import process,resolve
from harness.datadog_backend.wire import msgpack
from harness.datadog_integrations import dbm_assertions as checks
from harness.datadog_integrations.lab import AgentLab,REVISION,base_parser,resolve_args,output_dir,receipt,attach_artifacts,write_results,wait_backend_spans
from harness.datadog_telemetry.probe import sha

MARIADB_DIGEST='sha256:1292844148b311e4ed4300022a996d39083f415a963e970cf47cad1b3b18e3a6'

def server_command(root):
    root=Path(root)
    # RBE containers run as root; MariaDB requires an explicit user in that case.
    # Keep the fixture under the executor's existing identity on either platform.
    user_args = ['--user=root'] if os.geteuid() == 0 else []
    return [str(root/'lib64/ld-linux-x86-64.so.2'),'--library-path',str(root/'lib/x86_64-linux-gnu')+':'+str(root/'usr/lib/x86_64-linux-gnu'),str(root/'usr/sbin/mariadbd'),'--no-defaults'] + user_args + [
            '--basedir='+str(root/'usr'),'--lc-messages-dir='+str(root/'usr/share/mariadb'),'--plugin-dir='+str(root/'usr/lib/mysql/plugin')]

@contextmanager
def mariadb(root,out):
    data=out/'mysql-data'
    if data.exists():shutil.rmtree(data)
    data.mkdir()
    sql='CREATE DATABASE mysql;\nUSE mysql;\n'+(Path(root)/'usr/share/mariadb/mariadb_system_tables.sql').read_text()
    # Bootstrap creates actual InnoDB/MyISAM system tables from the pinned image.
    bootstrap=server_command(root)+['--bootstrap','--skip-grant-tables','--datadir='+str(data),'--tmpdir='+str(out),'--innodb-buffer-pool-size=32M']
    with (out/'mariadb-bootstrap.log').open('wb') as log:
        proc=subprocess.run(bootstrap,input=sql.encode(),stdout=log,stderr=subprocess.STDOUT,timeout=60)
    assert proc.returncode==0,(out/'mariadb-bootstrap.log').read_text()
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    query_log=out/'mysql-queries.log'
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='dbm-') as socket_dir:
        command=server_command(root)+['--skip-grant-tables','--datadir='+str(data),'--tmpdir='+str(out),'--innodb-buffer-pool-size=32M',
            '--bind-address=127.0.0.1','--port='+str(port),'--socket='+str(Path(socket_dir)/'mysql.sock'),'--pid-file='+str(out/'mysql.pid'),
            '--general-log=1','--general-log-file='+str(query_log)]
        with process(command,out/'mariadb.log',dict(os.environ)) as proc:
            deadline=time.monotonic()+30
            while True:
                assert proc.poll() is None and time.monotonic()<deadline,(out/'mariadb.log').read_text()
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.1)
            yield port,query_log

def execute(args,out):
    results=[];evidence={};all_records={};all_env={}
    with mariadb(args.mariadb_rootfs,out) as (port,query_log),AgentLab(args,out) as lab:
        try:
            for mode in ('disabled','service','full','dynamic_service'):
                case=out/mode
                offset=query_log.stat().st_size
                records,env=lab.run_workload(case,[args.app,'--identity-file',str(case/'identity.json'),'--sdk-overlay',args.sdk_overlay,
                    '--mode',mode,'--port',str(port)],{'DD_SERVICE':'dbm-lab','DD_ENV':'dbm-env','DD_VERSION':'dbm-version',
                    'DD_TRACE_API_VERSION':'v0.4','DD_DBM_PROPAGATION_MODE':mode,'DD_TRACE_SAMPLING_RULES':'[{"sample_rate":1}]',
                    'DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED':'true'},'datadog-dbm-'+mode)
                assert records and all(r['status']==200 for r in records),records
                spans=[]
                for r in records:
                    raw=(case/'tracer'/r['raw_file']).read_bytes();assert sha(raw)==r['raw_sha256']
                    if r['path']=='/v0.4/traces':spans.extend(s for trace in msgpack(raw) for s in trace)
                ids={s['span_id'] for s in spans}
                backend=wait_backend_spans(lab,lambda ss:ids<={s['span_id'] for s in ss})
                backend=[s for s in backend if s['span_id'] in ids]
                identity=json.loads((case/'identity.json').read_text())
                with query_log.open('rb') as log:log.seek(offset);queries=log.read().decode()
                (case/'sql.log').write_text(queries)
                (case/'events.json').write_text(json.dumps({'native':spans,'backend':backend},indent=2)+'\n')
                evidence[mode]=(identity,queries,spans,backend);all_records[mode]=records;all_env[mode]=env
            for feature,check in checks.CHECKS.items():
                result=receipt(feature,[feature],all_env['full'],'full/tracer/requests.json',sha((out/'full/tracer/requests.json').read_bytes()),out,
                    clientVersion='1.1.2',databaseImageDigest=MARIADB_DIGEST,workloadSha256=sha(Path(args.app).read_bytes()),
                    sourceSha256='0f020d887f4bf57e0428cad0ee1f460ce23e062bf1e0b60c2c9eaba57a3445c8' if feature=='mysql_support' else 'fec905b50afb37d2b72a908b4fb98cdbecf70a464bb614efb72b5783ab1b9e76',
                    source='https://github.com/DataDog/system-tests/blob/'+REVISION+'/tests/integrations/'+('test_db_integrations_sql.py' if feature=='mysql_support' else 'test_dbm.py'),
                    missingAssertions=['MariaDB/PyMySQL exercised; upstream PostgreSQL and other language drivers are not exercised.'])
                results.append(result)
                for mode in evidence:
                    attach_artifacts(result,out,[mode+'/identity.json',mode+'/sql.log',mode+'/events.json',mode+'/tracer/requests.json'])
                    result['artifacts'].extend({'file':mode+'/tracer/'+r['raw_file'],'sha256':r['raw_sha256']} for r in all_records[mode])
                attach_artifacts(result,out,['mariadb-bootstrap.log','datadog.yaml','agent-info.json'])
                try:check(evidence);result['status']='passed';print(feature,'passed',flush=True)
                except Exception as error:result['detail']=repr(error);raise
        finally:
            (out/'backend-capture.json').write_text(json.dumps(lab.backend.snapshot(),indent=2)+'\n')
            for result in results:
                attach_artifacts(result,out,['backend-capture.json'])
                result['artifacts'].extend({'file':'backend/'+r['raw_file'],'sha256':r['raw_sha256']} for r in lab.backend.snapshot())
            write_results(out,'datadog-dbm-results.json',results)
    assert len(results)==4 and all(r['status']=='passed' for r in results)

def main():
    if not __debug__:raise RuntimeError('DBM assertions require optimization disabled')
    parser=base_parser();parser.add_argument('--mariadb-rootfs',required=True)
    args=resolve_args(parser.parse_args());args.mariadb_rootfs=resolve(args.mariadb_rootfs)
    execute(args,output_dir())
if __name__=='__main__':main()
