"""Actual OTEL PyMySQL automatic instrumentation through Datadog's OTEL provider."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
parser=argparse.ArgumentParser()
for option in ('sdk-overlay','identity-file','port','mode'):
    parser.add_argument('--'+option,required=True)
args=parser.parse_args();sys.path.insert(0,args.sdk_overlay)
import pymysql
import ddtrace
from ddtrace import tracer
from opentelemetry import trace
from ddtrace.opentelemetry import TracerProvider
from opentelemetry.instrumentation.pymysql import PyMySQLInstrumentor
trace.set_tracer_provider(TracerProvider())
assert not getattr(pymysql,'_datadog_patch',False),'Native PyMySQL tracing must be disabled for OTEL control'
if args.mode=='enabled':PyMySQLInstrumentor().instrument()
identity={'tracer_version':ddtrace.__version__,'client_version':importlib.metadata.version('pymysql'),'mode':args.mode,
          'instrumentation_version':importlib.metadata.version('opentelemetry-instrumentation-pymysql'),
          'api_version':importlib.metadata.version('opentelemetry-api'),'port':int(args.port),'queries':[]}
conn=pymysql.connect(host='127.0.0.1',port=int(args.port),user='root',database='mysql',autocommit=True)
with trace.get_tracer('mysql.lab').start_as_current_span('otel.mysql.control') as control:
    context=control.get_span_context();identity.update(trace_id=str(context.trace_id),parent_id=str(context.span_id))
    with conn.cursor() as cursor:
        for query in ['DROP TABLE IF EXISTS otel_mysql_lab','CREATE TABLE otel_mysql_lab (id INTEGER PRIMARY KEY, value VARCHAR(32))',
                      "INSERT INTO otel_mysql_lab VALUES (1, 'hello-secret'), (2, 'world-secret')",'SELECT id, value FROM otel_mysql_lab ORDER BY id']:
            rows=cursor.execute(query);values=cursor.fetchall();identity['queries'].append({'query':query,'rowcount':rows,'result':values})
        assert identity['queries'][-1]['result']==((1,'hello-secret'),(2,'world-secret'))
        try:cursor.execute('SELECT invalid_column FROM otel_mysql_lab')
        except pymysql.err.OperationalError as error:
            assert error.args[0]==1054
            identity['error']={'type':type(error).__name__,'message':str(error)}
        else:raise AssertionError('Expected genuine MariaDB invalid-column error')
conn.close();tracer.flush()
Path(args.identity_file).write_text(json.dumps(identity,indent=2)+'\n')
