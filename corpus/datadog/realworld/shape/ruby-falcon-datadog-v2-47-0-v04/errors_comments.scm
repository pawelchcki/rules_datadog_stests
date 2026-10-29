; The native Datadog traces of the errors_comments scenario, reviewed for ruby-falcon-datadog-v2-47-0-v04.
; Builders: corpus/datadog/shape/falcon.scm; rendered by tools/datadog_shapes.py.
(define-library (datadog realworld shape ruby-falcon-datadog-v2-47-0-v04 errors_comments)
  (export scenario-shape)
  (import (scheme base) (datadog trace-shape) (datadog shape falcon))
  (begin

(define scenario-shape
  (traces falcon-app
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
          (sequel "SELECT count(*) AS 'count' FROM `favorites` WHERE (`article_id` = :article) LIMIT 1"))))
    (trace
      (sinatra-request "DELETE" "/api/articles/:slug" 204
        (url "/api/articles/err-comment-art-rules_stests_<workload>")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (sequel "DELETE FROM `articles` WHERE (`id` = :id)")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
    (trace
      (sinatra-request "GET" "/api/articles/:slug/comments" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>/comments")
        (tag "_dd.tracing.transport" "ruby")
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))))
    (trace
      (span "rack.request" "POST /api/articles/:slug/comments"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "POST")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/:slug/comments")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/some-slug/comments")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "POST /api/articles/:slug/comments"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "POST")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/some-slug/comments")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/:slug/comments")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "POST /api/articles/:slug/comments"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/:slug/comments")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "POST" "/api/articles/:slug/comments" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>/comments")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
    (trace
      (sinatra-request "POST" "/api/articles/:slug/comments" 422
        (url "/api/articles/err-comment-art-rules_stests_<workload>/comments")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:body=>[\"can't be blank\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
    (trace
      (span "rack.request" "DELETE /api/articles/:slug/comments/:comment_id"
        (span-type "web")
        (tag "_dd.tracing.transport" "ruby")
        (tag "component" "rack")
        (tag "http.base_url" "http://<endpoint>")
        (tag "http.method" "DELETE")
        (tag "http.response.headers.content-type" "application/json; charset=utf-8")
        (tag "http.route" "/api/articles/:slug/comments/:comment_id")
        (tag "http.status_code" "401")
        (tag "http.url" "/api/articles/some-slug/comments/1")
        (tag "http.useragent" "hurl/8.0.1")
        (tag "operation" "request")
        (tag "span.kind" "server")
        (tag "version" "1")
        (metric "_dd.measured" 1)
        (span "sinatra.request" "DELETE /api/articles/:slug/comments/:comment_id"
          (span-type "web")
          (tag "component" "sinatra")
          (tag "http.method" "DELETE")
          (tag "http.response.headers.content-type" "application/json; charset=utf-8")
          (tag "http.status_code" "401")
          (tag "http.url" "/api/articles/some-slug/comments/1")
          (tag "operation" "request")
          (tag "sinatra.route.path" "/api/articles/:slug/comments/:comment_id")
          (tag "version" "1")
          (metric "_dd.measured" 1)
          (span "sinatra.route" "DELETE /api/articles/:slug/comments/:comment_id"
            (span-type "web")
            (raised "RealWorld::Failure" "{:token=>[\"is missing\"]}")
            first-finished
            (tag "_dd.sdk.otlp_export" "false")
            (tag "component" "sinatra")
            (tag "operation" "route")
            (tag "sinatra.app.name" "RealWorld::App")
            (tag "sinatra.route.path" "/api/articles/:slug/comments/:comment_id")
            (tag "version" "1")
            (metric "_dd.measured" 1)))))
    (trace
      (sinatra-request "DELETE" "/api/articles/:slug/comments/:comment_id" 404
        (url "/api/articles/err-comment-art-rules_stests_<workload>/comments/99999")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:comment=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1")
          (sequel "SELECT * FROM `comments` WHERE ((`article_id` = :article) AND (`id` = :id)) LIMIT 1"))))
    (trace
      (sinatra-request "DELETE" "/api/articles/:slug/comments/:comment_id" 404
        (url "/api/articles/unknown-slug-rules_stests_<workload>/comments/99999")
        (tag "_dd.tracing.transport" "ruby")
        (before-filter
          (sequel "SELECT * FROM `users` WHERE (`id` = :id) LIMIT 1"
            first-finished
            (tag "_dd.sdk.otlp_export" "false")))
        (route
          (failure "{:article=>[\"not found\"]}")
          (sequel "SELECT * FROM `articles` WHERE (`slug` = :slug) LIMIT 1"))))
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
