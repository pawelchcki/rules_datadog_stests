; The native Datadog traces of the errors_articles scenario, reviewed for ruby-falcon-datadog-v2-47-0-v04.
; Builders: corpus/datadog/shape/falcon.scm; rendered by tools/datadog_shapes.py.
(define-library (datadog realworld shape ruby-falcon-datadog-v2-47-0-v04 errors_articles)
  (export scenario-shape)
  (import (scheme base) (datadog trace-shape) (datadog shape falcon))
  (begin

(define scenario-shape
  (traces falcon-app
    (repeat 2
      (trace
        (sinatra-request "POST" "/api/articles" 201
          (url "/api/articles")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (sequel "DELETE FROM `article_tags` WHERE (`article_id` = :article)")
            (sequel "INSERT INTO `articles` (`user_id`, `slug`, `title`, `description`, `body`, `created_at`, `updated_at`) VALUES (:user_id, :slug, :title, :description, :body, :created_at, :updated_at)")
            (sequel "SELECT * FROM `articles` WHERE (`id` = :id) LIMIT 1")
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1")
            (sequel "SELECT 1 FROM `articles` WHERE (`slug` = :slug) LIMIT 1")
            (sequel "SELECT 1 FROM `favorites` WHERE ((`article_id` = :article) AND (`user_id` = :user)) LIMIT 1")
            (sequel "SELECT 1 FROM `follows` WHERE ((`follower_id` = :follower) AND (`followed_id` = :followed)) LIMIT 1")
            (sequel "SELECT `name` FROM `tags` WHERE (`id` IN (SELECT `tag_id` FROM `article_tags` WHERE (`article_id` = :article))) ORDER BY `id`")
            (sequel "SELECT count(*) AS 'count' FROM `favorites` WHERE (`article_id` = :article) LIMIT 1")))))
    (trace
      (span "rack.request" "POST /api/articles"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "POST")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "POST /api/articles"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "POST")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "POST /api/articles"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "POST" "/api/articles" 422
        (url "/api/articles")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:body=>[\"can't be blank\"]}"))))
    (trace
      (sinatra-request "POST" "/api/articles" 422
        (url "/api/articles")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:description=>[\"can't be blank\"]}"))))
    (trace
      (sinatra-request "POST" "/api/articles" 422
        (url "/api/articles")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:title=>[\"can't be blank\"]}"))))
    (repeat 2
      (trace
        (sinatra-request "DELETE" "/api/articles/:slug" 204
          (url "/api/articles/dup-title-rules_stests_<workload>")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (sequel "DELETE FROM `articles` WHERE (`id` = :id)")
            (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1")))))
    (trace
      (span "rack.request" "DELETE /api/articles/:slug"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "DELETE")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/:slug")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/some-slug")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "DELETE /api/articles/:slug"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "DELETE")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/some-slug")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/:slug")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "DELETE /api/articles/:slug"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/:slug")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "DELETE" "/api/articles/:slug" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
    (trace
      (sinatra-request "GET" "/api/articles/:slug" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (span "rack.request" "PUT /api/articles/:slug"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "PUT")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/:slug")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/some-slug")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "PUT /api/articles/:slug"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "PUT")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/some-slug")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/:slug")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "PUT /api/articles/:slug"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/:slug")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (repeat 2
      (trace
        (sinatra-request "PUT" "/api/articles/:slug" 404
          (url "/api/articles/unknown-slug-rules_stests_<workload>")
          (tag "_dd.tracing.transport" "ruby")
          (before-filter
            (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
              first-finished
              (tag "_dd.sdk.otlp_export" "false")))
          (route
            (failure "{:article=>[\"not found\"]}")
            (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1")))))
    (trace
      (span "rack.request" "DELETE /api/articles/:slug/favorite"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "DELETE")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/:slug/favorite")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/some-slug/favorite")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "DELETE /api/articles/:slug/favorite"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "DELETE")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/some-slug/favorite")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/:slug/favorite")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "DELETE /api/articles/:slug/favorite"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/:slug/favorite")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "DELETE" "/api/articles/:slug/favorite" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>/favorite")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
    (trace
      (span "rack.request" "POST /api/articles/:slug/favorite"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "POST")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/:slug/favorite")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/some-slug/favorite")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "POST /api/articles/:slug/favorite"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "POST")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/some-slug/favorite")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/:slug/favorite")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "POST /api/articles/:slug/favorite"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/:slug/favorite")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "POST" "/api/articles/:slug/favorite" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>/favorite")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
    (trace
      (span "rack.request" "GET /api/articles/feed"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "GET")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/feed")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/feed")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "GET /api/articles/feed"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "GET")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/feed")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/feed")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "GET /api/articles/feed"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/feed")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
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
