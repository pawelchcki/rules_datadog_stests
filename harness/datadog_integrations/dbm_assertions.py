"""Correlate actual server query logs with native and agent SQL spans."""
import re

def sql_spans(spans):
    return [s for s in spans if s['name']=='pymysql.query' and s['resource']=='SELECT @@version']

def check_mysql_spans(evidence):
    for mode,(identity,queries,native,backend) in evidence.items():
        assert identity['client_version']=='1.1.2' and 'MariaDB' in identity['queries'][-1]['result'][0][0]
        assert identity['queries'][3]['result']==[[1,'first'],[2,'second']]
        for spans in (native,backend):
            candidates=sql_spans(spans);assert len(candidates)==1,candidates
            span=candidates[0]
            assert span['parent_id']==int(identity['parent_id'])
            assert span['trace_id']==int(identity['trace_id'])&((1<<64)-1)
            assert span['type']=='sql' and span['duration']>0 and span.get('error',0)==0
            assert span['meta']['db.name']=='mysql' and span['meta']['db.user']=='root'
            assert span['meta']['out.host']=='127.0.0.1'
            assert span['metrics']['network.destination.port']==identity['port']
            assert span['metrics']['db.row_count']==1

def actual_query(queries):
    candidates=[q for q in queries.splitlines() if 'Query\t' in q and q.endswith('SELECT @@version')]
    assert len(candidates)==1,candidates
    return candidates[0].split('Query\t',1)[1]

def check_dbm_comment(evidence):
    for mode,(identity,queries,native,backend) in evidence.items():
        query=actual_query(queries)
        if mode=='disabled':
            assert query=='SELECT @@version'
        else:
            assert query.startswith('/*') and '*/ SELECT @@version' in query,query
            for field in ("dddbs='pymysql'","ddps='dbm-lab'","ddpv='dbm-version'","dde='dbm-env'","dddb='mysql'","ddh='127.0.0.1'"):
                assert field in query,(field,query)
            assert ('traceparent=' in query)==(mode=='full')

def check_dbm_correlation(evidence):
    for mode,(identity,queries,native,backend) in evidence.items():
        query=actual_query(queries)
        for spans in (native,backend):
            span=sql_spans(spans)[0]
            if mode=='full':
                match=re.search("traceparent='00-([0-9a-f]{32})-([0-9a-f]{16})-01'",query)
                assert match,query
                assert int(match[1],16)==int(identity['trace_id'])
                assert int(match[2],16)==span['span_id']
                assert span['meta']['_dd.dbm_trace_injected']=='true'
            else:
                assert 'traceparent=' not in query
                assert '_dd.dbm_trace_injected' not in span['meta']

def check_dbm_dynamic_service(evidence):
    identity,queries,native,backend=evidence['dynamic_service']
    query=actual_query(queries)
    match=re.search("ddsh='([0-9]+)'",query);assert match and int(match[1])>0,query
    for spans in (native,backend):
        span=sql_spans(spans)[0]
        assert span['meta']['_dd.propagated_hash']==match[1]
    assert 'ddsh=' not in actual_query(evidence['disabled'][1])

CHECKS={'database_monitoring_support':check_dbm_comment,'database_monitoring_correlation':check_dbm_correlation,
        'database_monitoring_dynamic_service':check_dbm_dynamic_service,'mysql_support':check_mysql_spans}
