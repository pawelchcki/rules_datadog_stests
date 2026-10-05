"""Genuine manual DSM checkpoints and botocore produce/consume operations."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
parser=argparse.ArgumentParser()
for name in ('sdk-overlay','api-url','identity-file','mode'):
    parser.add_argument('--'+name,required=True)
args=parser.parse_args();sys.path.insert(0,args.sdk_overlay)
import boto3
from botocore.config import Config
import ddtrace
from ddtrace import tracer
from ddtrace.data_streams import set_produce_checkpoint,set_consume_checkpoint
from ddtrace.internal.datastreams import data_streams_processor
if args.mode=='enabled':
    # Injection starts before the locked client overlay is on sys.path. Register
    # the SDK's own optional botocore hooks now that the real dependency exists.
    import ddtrace.internal.datastreams.botocore

ddtrace.patch(botocore=True)
processor=data_streams_processor()
identity={'tracer_version':ddtrace.__version__,'client_version':boto3.__version__,
          'botocore_version':importlib.metadata.version('botocore'),'mode':args.mode,'manual':{},'calls':[]}
if args.mode=='enabled':
    processor.new_pathway()
    carrier={}
    produced=set_produce_checkpoint('dd-streams','system-tests-queue',carrier.__setitem__)
    consumed=set_consume_checkpoint('dd-streams','system-tests-queue',carrier.get)
    identity['manual']={'carrier':carrier,'producer_hash':produced.closest_opposite_direction_hash or int.from_bytes(__import__('base64').b64decode(carrier['dd-pathway-ctx-base64'])[:8],'little'), 'consumer_hash':consumed.hash}
    processor.new_pathway()
    injected=processor.set_checkpoint(['direction:out','topic:dsm-injection-topic','type:kafka'])
    identity['injection']={'carrier':{'dd-pathway-ctx-base64':injected.encode_b64()},'hash':injected.hash}
    # Fixed upstream producer carrier separately exercises decoding rather than reusing encoder output.
    fixed='nMKD2ZEAtFOy/f/K5mOy/f/K5mM='
    extracted=processor.decode_pathway_b64(fixed)
    parent=extracted.hash
    extracted.set_checkpoint(['direction:in','topic:dsm-injection-topic','type:kafka'])
    identity['extraction']={'carrier':fixed,'parent_hash':parent,'hash':extracted.hash}

clients={name:boto3.client(name,endpoint_url=args.api_url,region_name='us-east-1',aws_access_key_id='local-test',
    aws_secret_access_key='local-test',config=Config(retries={'max_attempts':0},proxies={})) for name in ('sqs','sns','kinesis')}
queue=args.api_url+'/000000000000/sqs-queue'
sns_queue=args.api_url+'/000000000000/sns-queue'
topic='arn:aws:sns:us-east-1:000000000000:local-topic'
stream='arn:aws:kinesis:us-east-1:000000000000:stream/local-stream'
for service in ('sqs','sns','kinesis'):
    if processor:processor.new_pathway()
    with tracer.trace('dsm.lab.produce',resource=service) as parent:
        record={'service':service,'trace_id':str(parent.trace_id),'parent_id':str(parent.span_id)}
        if service=='sqs':
            clients['sqs'].send_message(QueueUrl=queue,MessageBody='local-sqs-payload')
        elif service=='sns':
            clients['sns'].publish(TopicArn=topic,Message='local-sns-payload')
        else:
            clients['kinesis'].put_record(StreamARN=stream,Data=json.dumps({'message':'local-kinesis-payload'}),PartitionKey='local')
    # A fresh consumer context must be restored from the actual delivered carrier.
    tracer.context_provider.activate(None)
    if service in ('sqs','sns'):
        response=clients['sqs'].receive_message(QueueUrl=queue if service=='sqs' else sns_queue)
        assert response['Messages'][0]['Body']=='local-'+service+'-payload'
    else:
        response=clients['kinesis'].get_records(ShardIterator='local',StreamARN=stream)
        assert json.loads(response['Records'][0]['Data'])['message']=='local-kinesis-payload'
    tracer.context_provider.activate(None)
    record['response']=str(response)
    identity['calls'].append(record)
if processor:processor.periodic()
tracer.flush()
Path(args.identity_file).write_text(json.dumps(identity,indent=2)+'\n')
