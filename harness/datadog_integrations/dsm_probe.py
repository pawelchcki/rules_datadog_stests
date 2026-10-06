"""Run nine distinct DSM/AWS checks with real botocore and native wire captures."""
import json
from pathlib import Path
from harness.datadog_agent.probe import server_thread
from harness.datadog_backend.backend import decompress
from harness.datadog_backend.wire import msgpack
from harness.datadog_integrations import dsm_assertions as checks,fake_aws
from harness.datadog_integrations.lab import AgentLab,REVISION,base_parser,resolve_args,output_dir,receipt,attach_artifacts,write_results,wait_backend_spans
from harness.datadog_telemetry.probe import sha
from harness.datadog_telemetry.probe import TelemetryBackendHandler

class DsmBackendHandler(TelemetryBackendHandler):
    def do_POST(self):
        if self.path != '/api/v0.1/pipeline_stats':
            return super().do_POST()
        raw=self.rfile.read(int(self.headers['Content-Length']))
        headers={k.lower():v for k,v in self.headers.items()}
        from harness.datadog_backend.backend import API_KEY
        assert headers['dd-api-key']==API_KEY
        payload=msgpack(decompress(raw,headers.get('content-encoding','')))
        assert payload['Lang']=='python' and payload['TracerVersion']=='4.15.5'
        self.server.capture(self.path,headers,raw,200,payload,None)
        self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')

def execute(args,out):
    results=[]
    with AgentLab(args,out) as lab:
        lab.backend.RequestHandlerClass=DsmBackendHandler
        aws=fake_aws.server(out/'aws')
        with server_thread(aws):
            try:
                evidence={}
                for mode in ('enabled','disabled'):
                    case=out/mode
                    first=len(aws.snapshot())
                    records,env=lab.run_workload(case,[args.app,'--identity-file',str(case/'identity.json'),'--sdk-overlay',args.sdk_overlay,
                        '--api-url','http://127.0.0.1:'+str(aws.server_port),'--mode',mode],
                        {'DD_SERVICE':'dsm-lab','DD_ENV':'dsm-env','DD_VERSION':'dsm-version','DD_TRACE_API_VERSION':'v0.4',
                         'DD_DATA_STREAMS_ENABLED':str(mode=='enabled').lower(),'_DD_TRACE_STATS_WRITER_INTERVAL':'3600',
                         'DD_TRACE_PROPAGATION_STYLE_EXTRACT':'datadog',
                         'DD_BOTOCORE_PROPAGATION_ENABLED':'true',
                         'DD_TRACE_SAMPLING_RULES':'[{"sample_rate":1}]'},'datadog-dsm-'+mode)
                    assert records and all(r['status']==200 for r in records),records
                    points=[];spans=[]
                    for r in records:
                        raw=(case/'tracer'/r['raw_file']).read_bytes();assert sha(raw)==r['raw_sha256']
                        if r['path']=='/v0.1/pipeline_stats':
                            payload=msgpack(decompress(raw,r['headers'].get('content-encoding','')))
                            assert payload['Service']=='dsm-lab' and payload['TracerVersion'] == '4.15.5'
                            points.extend(p for bucket in payload['Stats'] for p in bucket['Stats'])
                        elif r['path']=='/v0.4/traces':spans.extend(s for trace in msgpack(raw) for s in trace)
                    identity=json.loads((case/'identity.json').read_text())
                    assert identity['client_version']=='1.38.22' and identity['tracer_version'] == '4.15.5'
                    (case/'events.json').write_text(json.dumps({'points':points,'spans':spans},indent=2)+'\n')
                    evidence[mode]=(identity,aws.snapshot()[first:],points,spans,records,env)
                assert not evidence['disabled'][2],'disabled control emitted DSM stats'
                identity,aws_records,points,spans,records,env=evidence['enabled']
                for request in [r for r in records if r['path']=='/v0.1/pipeline_stats']:
                    forwarded=[r for r in lab.backend.snapshot() if r['path']=='/api/v0.1/pipeline_stats' and r['raw_sha256']==request['raw_sha256']]
                    assert len(forwarded)==1 and forwarded[0]['status']==200
                    assert forwarded[0]['headers']['via']=='trace-agent 7.83.1'
                backend=wait_backend_spans(lab,lambda ss:{s['span_id'] for s in spans}<={s['span_id'] for s in ss})
                backend=[s for s in backend if s['span_id'] in {x['span_id'] for x in spans}]
                (out/'backend-spans.json').write_text(json.dumps(backend,indent=2)+'\n')
                for feature,check in checks.CHECKS.items():
                    result=receipt(feature,[feature],env,'enabled/tracer/requests.json',sha((out/'enabled/tracer/requests.json').read_bytes()),out,
                        clientVersion=identity['client_version'],workloadSha256=sha(Path(args.app).read_bytes()),
                        sourceSha256='30149293f2695b832318604fe359cf15508f4a0dcdaf640779a6f1dc3e7c4215',
                        source='https://github.com/DataDog/system-tests/blob/'+REVISION+'/tests/integrations/test_dsm.py',
                        missingAssertions=['Cross-process and cross-language buddy pairs are not exercised; loopback AWS wire peer models delivery.',
                                           'AWS infrastructure behavior beyond modeled message delivery is not asserted.'])
                    results.append(result)
                    attach_artifacts(result,out,['enabled/identity.json','enabled/events.json','disabled/identity.json','disabled/events.json',
                                                 'disabled/tracer/requests.json','backend-spans.json','agent-info.json','datadog.yaml'])
                    for mode in evidence:
                        result['artifacts'].extend({'file':mode+'/tracer/'+r['raw_file'],'sha256':r['raw_sha256']} for r in evidence[mode][4])
                    try:
                        if isinstance(check,tuple):
                            fn,service=check;fn(points,identity,aws_records,spans,service)
                            if fn==checks.check_aws_propagation:fn(points,identity,aws_records,backend,service)
                        else:check(points,identity)
                        result['status']='passed';print(feature,'passed',flush=True)
                    except Exception as error:result['detail']=repr(error);raise
            finally:
                for name,records in (('aws',aws.snapshot()),('backend',lab.backend.snapshot())):
                    path=out/(name+'-capture.json');path.write_text(json.dumps(records,indent=2)+'\n')
                    for result in results:
                        attach_artifacts(result,out,[path.name])
                        result['artifacts'].extend({'file':name+'/'+r['raw_file'],'sha256':r['raw_sha256']} for r in records)
                write_results(out,'datadog-dsm-results.json',results)
    assert len(results)==9 and all(r['status']=='passed' for r in results)

def main():
    if not __debug__:raise RuntimeError('DSM assertions require optimization disabled')
    execute(resolve_args(base_parser().parse_args()),output_dir())
if __name__=='__main__':main()
