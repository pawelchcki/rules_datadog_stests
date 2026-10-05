"""Loopback AWS wire peer retaining genuine boto3 requests and delivered carriers."""
import base64
import hashlib
import json
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs
from harness.datadog_backend.backend import BackendServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        target = self.headers.get('X-Amz-Target', '')
        if target:
            operation = target.rsplit('.', 1)[-1]
            request = json.loads(raw)
            service = 'kinesis' if 'Kinesis' in target else 'sqs'
        else:
            form = parse_qs(raw.decode())
            operation = form['Action'][0]
            service = 'sns'
            request = {k: v[0] for k, v in form.items()}
        if operation == 'SendMessage':
            attrs = request.get('MessageAttributes', {})
            self.server.queues[request['QueueUrl']] = {'MessageId':'local-message', 'ReceiptHandle':'local-receipt',
                'Body':request['MessageBody'], 'MessageAttributes':attrs}
            response = {'MessageId':'local-message','MD5OfMessageBody':hashlib.md5(request['MessageBody'].encode()).hexdigest()}
        elif operation == 'ReceiveMessage':
            response = {'Messages':[self.server.queues[request['QueueUrl']]]}
        elif operation == 'PutRecord':
            self.server.streams[request['StreamARN']] = request['Data']
            response = {'ShardId':'shardId-000000000000','SequenceNumber':'1'}
        elif operation == 'GetRecords':
            response = {'Records':[{'Data':self.server.streams[request['StreamARN']], 'PartitionKey':'local',
                'SequenceNumber':'1','ApproximateArrivalTimestamp':time.time()}], 'NextShardIterator':'local'}
        elif operation == 'Publish':
            value = request['MessageAttributes.entry.1.Value.BinaryValue']
            self.server.queues[self.server.sns_queue] = {'MessageId':'sns-message','ReceiptHandle':'local-receipt',
                'Body':request['Message'],'MessageAttributes':{'_datadog':{'DataType':'Binary','BinaryValue':value}}}
            response = {'MessageId':'sns-message'}
        else:
            raise AssertionError((service, operation, request))
        headers = {k.lower():v for k,v in self.headers.items() if k.lower() not in ('authorization','x-amz-security-token')}
        self.server.capture('/'+service+'/'+operation,headers,raw,200,{'request':request,'response':response},None)
        if service == 'sns':
            encoded=b'<PublishResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/"><PublishResult><MessageId>sns-message</MessageId></PublishResult><ResponseMetadata><RequestId>local</RequestId></ResponseMetadata></PublishResponse>'
            content_type='text/xml'
        else:
            encoded=json.dumps(response).encode();content_type='application/x-amz-json-1.0'
        self.send_response(200)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(encoded)))
        self.send_header('x-amzn-RequestId','local')
        self.end_headers(); self.wfile.write(encoded)

def server(output):
    instance=BackendServer(('127.0.0.1',0),output)
    instance.RequestHandlerClass=Handler
    instance.queues={};instance.streams={}
    instance.sns_queue='http://127.0.0.1:'+str(instance.server_port)+'/000000000000/sns-queue'
    return instance
