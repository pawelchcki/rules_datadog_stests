# frozen_string_literal: true

# Build the middleware once before the threaded server accepts health checks.
# Concurrent cold Sinatra prototypes can lose Datadog's once-per-app Rack layer.
require File.join(ENV.fetch("REALWORLD_BUNDLE_ROOT"), "src/app/realworld")
RealWorld::App.prototype
