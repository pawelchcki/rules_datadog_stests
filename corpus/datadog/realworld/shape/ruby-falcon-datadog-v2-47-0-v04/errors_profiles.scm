; The native Datadog traces of the errors_profiles scenario, reviewed for ruby-falcon-datadog-v2-47-0-v04.
; Builders: corpus/datadog/shape/falcon.scm; rendered by tools/datadog_shapes.py.
(define-library (datadog realworld shape ruby-falcon-datadog-v2-47-0-v04 errors_profiles)
  (export scenario-shape)
  (import (scheme base) (datadog trace-shape) (datadog shape falcon))
  (begin

(define scenario-shape
  (traces falcon-app
    (trace
      (sinatra-request "GET" "/api/profiles/:username" 404
        (url "/api/profiles/unknown-user-rules_stests_<workload>")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:profile=>[\"not found\"]}")
          (sequel "SELECT * FROM `users` WHERE (`username` = :username) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (span "rack.request" "DELETE /api/profiles/:username/follow"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "DELETE")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/profiles/:username/follow")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/profiles/unknown-user-rules_stests_<workload>/follow")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "DELETE /api/profiles/:username/follow"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "DELETE")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/profiles/unknown-user-rules_stests_<workload>/follow")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/profiles/:username/follow")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "DELETE /api/profiles/:username/follow"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/profiles/:username/follow")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "DELETE" "/api/profiles/:username/follow" 404
        (url "/api/profiles/unknown-user-rules_stests_<workload>/follow")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:profile=>[\"not found\"]}")
          (sequel "SELECT * FROM `users` WHERE (`username` = :username) LIMIT 1"))))
    (trace
      (span "rack.request" "POST /api/profiles/:username/follow"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "POST")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/profiles/:username/follow")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/profiles/unknown-user-rules_stests_<workload>/follow")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "POST /api/profiles/:username/follow"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "POST")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/profiles/unknown-user-rules_stests_<workload>/follow")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/profiles/:username/follow")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "POST /api/profiles/:username/follow"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/profiles/:username/follow")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "POST" "/api/profiles/:username/follow" 404
        (url "/api/profiles/unknown-user-rules_stests_<workload>/follow")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:profile=>[\"not found\"]}")
          (sequel "SELECT * FROM `users` WHERE (`username` = :username) LIMIT 1"))))
    (trace
      (sinatra-request "POST" "/api/users" 201
        (url "/api/users")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (sequel "INSERT INTO `users` (`username`, `email`, `password_digest`, `created_at`, `updated_at`) VALUES (:username, :email, :password_digest, :created_at, :updated_at)")
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1")
          (sequel "SELECT 1 FROM `users` WHERE (`email` = :email) LIMIT 1")
          (sequel "SELECT 1 FROM `users` WHERE (`username` = :username) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))))
  ))
