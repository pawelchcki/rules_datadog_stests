(define-library (datadog realworld shape ruby-falcon-datadog-v2-47-0-v04 unicode)
  (export scenario-shape)
  (import (scheme base) (datadog trace-shape))
  (begin
(define scenario-shape
  '(
    (
      (count 1)
      (roots
        (
          (
            (native-fields
              ("duration" "error" "meta" "meta_struct" "metrics" "name" "parent_id" "resource" "service" "span_id" "span_links" "start" "trace_id" "type" ))
            (service "<service>")
            (name "rack.request")
            (type "web")
            (resource "GET /api/profiles/:username")
            (parent-kind "root")
            (error 0)
            (meta
              (
                ("_dd.p.dm" "-3")
                ("_dd.p.ksr" "1")
                ("_dd.p.tid" "<trace-id-high>")
                ("_dd.tracing.transport" "ruby")
                ("component" "rack")
                ("env" "test")
                ("http.base_url" "http://<endpoint>")
                ("http.method" "GET")
                ("http.response.headers.content-type" "application/json; charset=utf-8")
                ("http.route" "/api/profiles/:username")
                ("http.route.path" "")
                ("http.status_code" "404")
                ("http.url" "/api/profiles/%C3%BCn%C3%AFc%C3%B8de_rules_stests_<workload>")
                ("http.useragent" "")
                ("language" "ruby")
                ("operation" "request")
                ("runtime-id" "<runtime-id>")
                ("span.kind" "server")
                ("version" "1")))
            (metrics
              (
                ("_dd.limit_psr" 1.0)
                ("_dd.measured" 1.0)
                ("_dd.profiling.enabled" 0.0)
                ("_dd.rule_psr" 1.0)
                ("_dd.top_level" 1.0)
                ("_sampling_priority_v1" 2.0)
                ("process_id" "<process-id>")))
            (children
              (
                (
                  (native-fields
                    ("duration" "error" "meta" "meta_struct" "metrics" "name" "parent_id" "resource" "service" "span_id" "span_links" "start" "trace_id" "type" ))
                  (service "<service>")
                  (name "sinatra.request")
                  (type "web")
                  (resource "GET /api/profiles/:username")
                  (parent-kind "child")
                  (error 0)
                  (meta
                    (
                      ("component" "sinatra")
                      ("env" "test")
                      ("http.method" "GET")
                      ("http.response.headers.content-type" "application/json; charset=utf-8")
                      ("http.status_code" "404")
                      ("http.url" "/api/profiles/%C3%BCn%C3%AFc%C3%B8de_rules_stests_<workload>")
                      ("operation" "request")
                      ("sinatra.route.path" "/api/profiles/:username")
                      ("version" "1")))
                  (metrics
                    (
                      ("_dd.measured" 1.0)))
                  (children
                    (
                      (
                        (native-fields
                          ("duration" "error" "meta" "meta_struct" "metrics" "name" "parent_id" "resource" "service" "span_id" "span_links" "start" "trace_id" "type" ))
                        (service "<service>")
                        (name "sinatra.route")
                        (type "web")
                        (resource "GET /api/profiles/:username")
                        (parent-kind "child")
                        (error 1)
                        (meta
                          (
                            ("component" "sinatra")
                            ("env" "test")
                            ("error.message" "{:profile=>[\"not found\"]}")
                            ("error.stack" "<validated-stack>")
                            ("error.type" "RealWorld::Failure")
                            ("operation" "route")
                            ("sinatra.app.name" "RealWorld::App")
                            ("sinatra.route.path" "/api/profiles/:username")
                            ("version" "1")))
                        (metrics
                          (
                            ("_dd.measured" 1.0)))
                        (children
                          (
                            (
                              (native-fields
                                ("duration" "error" "meta" "meta_struct" "metrics" "name" "parent_id" "resource" "service" "span_id" "span_links" "start" "trace_id" "type" ))
                              (service "sqlite")
                              (name "sequel.query")
                              (type "sql")
                              (resource "SELECT * FROM `users` WHERE (`username` = :username) LIMIT 1")
                              (parent-kind "child")
                              (error 0)
                              (meta
                                (
                                  ("_dd.base_service" "<service>")
                                  ("_dd.sdk.otlp_export" "false")
                                  ("_dd.svc_src" "sequel")
                                  ("_dd.tags.process" "entrypoint.workdir:main,entrypoint.name:server,entrypoint.basedir:bin,entrypoint.type:script,svc.user:true")
                                  ("component" "sequel")
                                  ("db.instance" "<fixture>/realworld.sqlite3")
                                  ("db.system" "sqlite")
                                  ("env" "test")
                                  ("operation" "query")
                                  ("sequel.db.name" "<fixture>/realworld.sqlite3")
                                  ("sequel.db.vendor" "sqlite")
                                  ("span.kind" "client")
                                  ("version" "1")))
                              (metrics
                                (
                                  ("_dd.top_level" 1.0)))
                              (children
                                ()))))))))))))))))
  ))
