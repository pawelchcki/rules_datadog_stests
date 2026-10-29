; The native Datadog traces of the errors_auth scenario, reviewed for ruby-falcon-datadog-v2-47-0-v04.
; Builders: corpus/datadog/shape/falcon.scm; rendered by tools/datadog_shapes.py.
(define-library (datadog realworld shape ruby-falcon-datadog-v2-47-0-v04 errors_auth)
  (export scenario-shape)
  (import (scheme base) (datadog trace-shape) (datadog shape falcon))
  (begin

(define scenario-shape
  (traces falcon-app
    (trace
      (span "rack.request" "GET /api/user"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "GET")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/user")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/user")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "GET /api/user"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "GET")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/user")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/user")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "GET /api/user"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/user")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (repeat 2
      (trace
        (sinatra-request "PUT" "/api/user" 200
          (url "/api/user")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1")
            (sequel "SELECT 1 FROM `users` WHERE ((`id` != :id) AND (`email` = :email)) LIMIT 1")
            (sequel "SELECT 1 FROM `users` WHERE ((`id` != :id) AND (`username` = :username)) LIMIT 1")
            (sequel "UPDATE `users` SET `password_digest` = :password_digest, `updated_at` = :updated_at WHERE (`id` = :id)")))))
    (trace
      (span "rack.request" "PUT /api/user"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "PUT")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/user")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/user")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "PUT /api/user"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "PUT")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/user")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/user")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "PUT /api/user"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/user")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (repeat 2
      (trace
        (sinatra-request "PUT" "/api/user" 422
          (url "/api/user")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (failure "{:email=>[\"can't be blank\"]}")
            (sequel "SELECT 1 FROM `users` WHERE ((`id` != :id) AND (`username` = :username)) LIMIT 1")))))
    (repeat 2
      (trace
        (sinatra-request "PUT" "/api/user" 422
          (url "/api/user")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (failure "{:password=>[\"can't be blank\"]}")))))
    (trace
      (sinatra-request "PUT" "/api/user" 422
        (url "/api/user")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:password=>[\"is too short (minimum is 8 characters)\"]}")
          (sequel "SELECT 1 FROM `users` WHERE ((`id` != :id) AND (`email` = :email)) LIMIT 1")
          (sequel "SELECT 1 FROM `users` WHERE ((`id` != :id) AND (`username` = :username)) LIMIT 1"))))
    (repeat 2
      (trace
        (sinatra-request "PUT" "/api/user" 422
          (url "/api/user")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (failure "{:username=>[\"can't be blank\"]}")
            (sequel "SELECT 1 FROM `users` WHERE ((`id` != :id) AND (`email` = :email)) LIMIT 1")))))
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
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (sinatra-request "POST" "/api/users" 409
        (url "/api/users")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:email=>[\"has already been taken\"]}")
          (sequel "SELECT 1 FROM `users` WHERE (`email` = :email) LIMIT 1")
          (sequel "SELECT 1 FROM `users` WHERE (`username` = :username) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (sinatra-request "POST" "/api/users" 409
        (url "/api/users")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:username=>[\"has already been taken\"]}")
          (sequel "SELECT 1 FROM `users` WHERE (`email` = :email) LIMIT 1")
          (sequel "SELECT 1 FROM `users` WHERE (`username` = :username) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (sinatra-request "POST" "/api/users" 422
        (url "/api/users")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:email=>[\"can't be blank\"]}")
          (sequel "SELECT 1 FROM `users` WHERE (`username` = :username) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (sinatra-request "POST" "/api/users" 422
        (url "/api/users")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:password=>[\"can't be blank\", \"is too short (minimum is 8 characters)\"]}")
          (sequel "SELECT 1 FROM `users` WHERE (`email` = :email) LIMIT 1")
          (sequel "SELECT 1 FROM `users` WHERE (`username` = :username) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (sinatra-request "POST" "/api/users" 422
        (url "/api/users")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:username=>[\"can't be blank\"]}")
          (sequel "SELECT 1 FROM `users` WHERE (`email` = :email) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (sinatra-request "POST" "/api/users/login" 401
        (url "/api/users/login")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:credentials=>[\"invalid\"]}")
          (sequel "SELECT * FROM `users` WHERE (`email` = :email) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (span "rack.request" "POST /api/users/login"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "POST")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/users/login")
        (tag "http.status_code" "422")
        (tag "http.url" "/api/users/login")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "POST /api/users/login"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "POST")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "422")
          (tag "http.url" "/api/users/login")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/users/login")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "POST /api/users/login"
            (span-type "web")
            (raised "RealWorld::Failure" "{:email=>[\"can't be blank\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/users/login")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (span "rack.request" "POST /api/users/login"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "POST")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/users/login")
        (tag "http.status_code" "422")
        (tag "http.url" "/api/users/login")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "POST /api/users/login"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "POST")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "422")
          (tag "http.url" "/api/users/login")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/users/login")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "POST /api/users/login"
            (span-type "web")
            (raised "RealWorld::Failure" "{:password=>[\"can't be blank\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/users/login")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))))
  ))
