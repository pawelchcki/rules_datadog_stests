"""OpenTelemetry mysql spans from genuine instrumented successful/failing database queries."""
import json
from pathlib import Path
from harness.datadog_agent.probe import resolve
from harness.datadog_backend.wire import msgpack
from harness.datadog_integrations.dbm_probe import mariadb,MARIADB_DIGEST
from harness.datadog_integrations.lab import AgentLab,REVISION,base_parser,resolve_args,output_dir,receipt,attach_artifacts,write_results,wait_backend_spans
from harness.datadog_telemetry.probe import sha

def check(evidence):
    for mode,(identity,native,backend) in evidence.items():
        assert identity['client_version']=='1.1.2' and identity['api_version']=='1.44.0'
        assert identity['instrumentation_version']=='0.65b0'
        assert identity['queries'][-1]['result']==[[1,'hello-secret'],[2,'world-secret']]
        assert identity['error']['type']=='OperationalError' and 'invalid_column' in identity['error']['message']
        for spans in (native,backend):
            sql=[s for s in spans if s['meta'].get('db.system')=='mysql']
            if mode=='disabled':assert not sql,sql;continue
            assert len(sql)==5,sql
            for span in sql:
                assert span['meta']['db.name']=='mysql'
                assert span['meta']['db.user']=='root'
                assert span['meta']['span.kind']=='client'
                assert span['trace_id']==int(identity['trace_id'])&((1<<64)-1)
                assert span['parent_id']==int(identity['parent_id'])
                assert span['duration']>0
            success=[s for s in sql if not s.get('error',0)]
            errors=[s for s in sql if s.get('error',0)]
            assert len(success)==4 and len(errors)==1
            failed=errors[0]
            assert 'invalid_column' in failed['meta']['error.message']
            assert 'OperationalError' in failed['meta']['error.type']
            assert 'OperationalError' in failed['meta']['error.stack']
            events=failed.get('span_events') or failed.get('meta_struct',{}).get('span_events') or failed['meta'].get('events')
            if isinstance(events,str):events=json.loads(events)
            assert events,failed
            event=next(e for e in events if e['name']=='exception')
            assert 'OperationalError' in event['attributes']['exception.type']
            assert 'invalid_column' in event['attributes']['exception.message']

def execute(args,out):
    results=[];evidence={};records_by_mode={};envs={}
    with mariadb(args.mariadb_rootfs,out) as (port,query_log),AgentLab(args,out) as lab:
        try:
            for mode in ('enabled','disabled'):
                case=out/mode
                records,env=lab.run_workload(case,[args.app,'--sdk-overlay',args.sdk_overlay,'--identity-file',str(case/'identity.json'),
                    '--mode',mode,'--port',str(port)],{'DD_SERVICE':'otel-mysql-lab','DD_ENV':'otel-mysql-env','DD_TRACE_API_VERSION':'v0.4',
                    'DD_TRACE_OTEL_ENABLED':'true','DD_TRACE_PYMYSQL_ENABLED':'false','DD_DBM_PROPAGATION_MODE':'disabled',
                    'DD_TRACE_SAMPLING_RULES':'[{"sample_rate":1}]'},'datadog-otel-mysql-'+mode)
                assert records and all(r['status']==200 for r in records),records
                spans=[]
                for r in records:
                    raw=(case/'tracer'/r['raw_file']).read_bytes();assert sha(raw)==r['raw_sha256']
                    if r['path']=='/v0.4/traces':spans.extend(s for trace in msgpack(raw) for s in trace)
                ids={s['span_id'] for s in spans};assert ids
                backend=wait_backend_spans(lab,lambda ss:ids<={s['span_id'] for s in ss});backend=[s for s in backend if s['span_id'] in ids]
                identity=json.loads((case/'identity.json').read_text())
                (case/'events.json').write_text(json.dumps({'native':spans,'backend':backend},indent=2)+'\n')
                evidence[mode]=(identity,spans,backend);records_by_mode[mode]=records;envs[mode]=env
            result=receipt('otel_mysql_support',['otel_mysql_support'],envs['enabled'],'enabled/tracer/requests.json',sha((out/'enabled/tracer/requests.json').read_bytes()),out,
                databaseImageDigest=MARIADB_DIGEST,clientVersion='opentelemetry-instrumentation-pymysql0.65b0',workloadSha256=sha(Path(args.app).read_bytes()),
                sourceSha256='99a4587c78984389f3e9d5c80662ae619e6338e09b5c2487a05f88973be657f8',
                source='https://github.com/DataDog/system-tests/blob/'+REVISION+'/tests/integrations/test_open_telemetry.py',
                missingAssertions=['Uses genuine OTEL PyMySQL instrumentation through Datadog OTEL provider and native Agent transport; OTLP exporter route is not exercised.',
                                   'db.connection_string/db.operation/db.sql.table and upstream obfuscation placeholder matrix are not asserted.',
                                   'Pinned ddtrace OTEL provider emits error.stack on the span and exception type/message on the event, but does not emit exception.stacktrace event attribute.'])
            results.append(result)
            for mode in evidence:
                attach_artifacts(result,out,[mode+'/identity.json',mode+'/events.json',mode+'/tracer/requests.json'])
                result['artifacts'].extend({'file':mode+'/tracer/'+r['raw_file'],'sha256':r['raw_sha256']} for r in records_by_mode[mode])
            attach_artifacts(result,out,['mariadb-bootstrap.log','agent-info.json','datadog.yaml'])
            try:check(evidence);result['status']='passed';print('otel_mysql_support passed',flush=True)
            except Exception as error:result['detail']=repr(error);raise
        finally:
            (out/'backend-capture.json').write_text(json.dumps(lab.backend.snapshot(),indent=2)+'\n')
            for result in results:
                attach_artifacts(result,out,['backend-capture.json'])
                result['artifacts'].extend({'file':'backend/'+r['raw_file'],'sha256':r['raw_sha256']} for r in lab.backend.snapshot())
            write_results(out,'datadog-otel-mysql-results.json',results)

def main():
    if not __debug__:raise RuntimeError('OTEL mysql assertions require optimization disabled')
    parser=base_parser();parser.add_argument('--mariadb-rootfs',required=True)
    args=resolve_args(parser.parse_args());args.mariadb_rootfs=resolve(args.mariadb_rootfs)
    execute(args,output_dir())
if __name__=='__main__':main()
