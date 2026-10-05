"""Pinned PyMySQL executes real SQL against the pinned MariaDB server."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
parser=argparse.ArgumentParser()
for name in ('sdk-overlay','identity-file','mode','port'):
    parser.add_argument('--'+name,required=True)
args=parser.parse_args();sys.path.insert(0,args.sdk_overlay)
import pymysql
import ddtrace
from ddtrace import tracer

if not getattr(pymysql, '_datadog_patch', False):
    ddtrace.patch(pymysql=True)
identity={'tracer_version':ddtrace.__version__,'client_version':importlib.metadata.version('pymysql'),
          'mode':args.mode,'port':int(args.port),'queries':[]}
conn=pymysql.connect(host='127.0.0.1',port=int(args.port),user='root',database='mysql',autocommit=True)
with tracer.trace('dbm.lab.control',resource=args.mode) as control:
    identity.update(trace_id=str(control.trace_id),parent_id=str(control.span_id))
    with conn.cursor() as cursor:
        for query in ['DROP TABLE IF EXISTS dbm_lab','CREATE TABLE dbm_lab (id INTEGER PRIMARY KEY, value VARCHAR(32))',
                      "INSERT INTO dbm_lab VALUES (1, 'first'), (2, 'second')",'SELECT id, value FROM dbm_lab ORDER BY id',
                      'SELECT @@version']:
            rows=cursor.execute(query);result=cursor.fetchall()
            identity['queries'].append({'query':query,'rowcount':rows,'result':result})
        assert identity['queries'][3]['result']==((1,'first'),(2,'second'))
        assert identity['queries'][3]['rowcount']==2
conn.close();tracer.flush()
Path(args.identity_file).write_text(json.dumps(identity,indent=2)+'\n')
