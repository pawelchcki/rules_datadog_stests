"""Check native DSM aggregation, encoding, independent carriers and AWS parenting."""
import base64
import json
import struct

MANUAL='datastreams_monitoring_support_for_manual_checkpoints'
INJECT='datastreams_monitoring_support_context_injection_base64'
EXTRACT='datastreams_monitoring_support_for_base64_encoding'

def point(points,tags,parent=None):
    found=[p for p in points if set(p['EdgeTags'])==set(tags) and (parent is None or p['ParentHash']==parent)]
    assert len(found)==1,(tags,parent,points)
    assert found[0]['Hash']>0 and found[0]['PathwayLatency'] and found[0]['EdgeLatency'],found
    return found[0]

def check_manual_checkpoints(points,identity,*_):
    produced=point(points,['direction:out','manual_checkpoint:true','topic:system-tests-queue','type:dd-streams'],0)
    consumed=point(points,['direction:in','manual_checkpoint:true','topic:system-tests-queue','type:dd-streams'],produced['Hash'])
    assert produced['Hash']==identity['manual']['producer_hash']
    assert consumed['Hash']==identity['manual']['consumer_hash']

def check_base64_injection(points,identity,*_):
    value=identity['injection']['carrier']['dd-pathway-ctx-base64']
    raw=base64.b64decode(value,validate=True)
    assert base64.b64encode(raw).decode()==value and len(raw)>8
    produced=point(points,['direction:out','topic:dsm-injection-topic','type:kafka'],0)
    assert struct.unpack('<Q',raw[:8])[0]==produced['Hash']==identity['injection']['hash']

def check_base64_extraction(points,identity,*_):
    assert identity['extraction']['carrier']=='nMKD2ZEAtFOy/f/K5mOy/f/K5mM='
    assert identity['extraction']['parent_hash']==6031446427375485596
    consumed=point(points,['direction:in','topic:dsm-injection-topic','type:kafka'],6031446427375485596)
    assert consumed['Hash']==identity['extraction']['hash']

def aws_carrier(records,service):
    operation={'sqs':'SendMessage','sns':'Publish','kinesis':'PutRecord'}[service]
    request=next(r['payload']['request'] for r in records if r['path']=='/'+service+'/'+operation)
    if service=='sqs':return json.loads(request['MessageAttributes']['_datadog']['StringValue'])
    if service=='sns':return json.loads(base64.b64decode(request['MessageAttributes.entry.1.Value.BinaryValue']))
    return json.loads(base64.b64decode(request['Data']))['_datadog']

def check_aws_dsm(points,identity,records,spans,service):
    topic={'sqs':'sqs-queue','sns':'arn:aws:sns:us-east-1:000000000000:local-topic',
           'kinesis':'arn:aws:kinesis:us-east-1:000000000000:stream/local-stream'}[service]
    produced=point(points,['direction:out','topic:'+topic,'type:'+service],0)
    consume_type='sqs' if service=='sns' else service
    consume_topic='sns-queue' if service=='sns' else topic
    consumed=point(points,['direction:in','topic:'+consume_topic,'type:'+consume_type],produced['Hash'])
    carrier=aws_carrier(records,service)
    assert struct.unpack('<Q',base64.b64decode(carrier['dd-pathway-ctx-base64'])[:8])[0]==produced['Hash']
    assert consumed['Hash']!=produced['Hash']
    assert produced['PayloadSize'] and consumed['PayloadSize']

def check_aws_propagation(points,identity,records,spans,service):
    operations={'sqs':('SendMessage','ReceiveMessage'),'sns':('Publish','ReceiveMessage'),'kinesis':('PutRecord','GetRecords')}
    producer_op,consumer_op=operations[service]
    producers=[s for s in spans if s['meta'].get('aws.operation')==producer_op]
    consumers=[s for s in spans if s['meta'].get('aws.operation')==consumer_op and s['trace_id'] in {p['trace_id'] for p in producers}]
    assert len(producers)==len(consumers)==1,(service,producers,consumers,spans)
    producer,consumer=producers[0],consumers[0]
    carrier=aws_carrier(records,service)
    assert int(carrier['x-datadog-trace-id'])==producer['trace_id']==consumer['trace_id']
    assert int(carrier['x-datadog-parent-id'])==producer['span_id']==consumer['parent_id']
    control=next(c for c in identity['calls'] if c['service']==service)
    assert producer['parent_id']==int(control['parent_id'])
    for span in (producer,consumer):
        assert span['duration']>0 and span.get('error',0)==0
        assert span['meta']['component']=='botocore'
        assert span['meta']['http.status_code']=='200'

CHECKS={MANUAL:check_manual_checkpoints,INJECT:check_base64_injection,EXTRACT:check_base64_extraction}
for service in ('sqs','sns','kinesis'):
    CHECKS['datastreams_monitoring_support_for_'+service]=(check_aws_dsm,service)
    CHECKS['aws_'+service+'_span_creationcontext_propagation_via_message_attributes_with_dd_trace']=(check_aws_propagation,service)
