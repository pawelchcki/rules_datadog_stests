# frozen_string_literal: true

require "timeout"

builds = Queue.new
entered = Queue.new
release = Queue.new
# Force concurrent cold builds to overlap. A warmed prototype never enters here
# on a request thread; this does not serialize the server's ordinary requests.
Sinatra::Base.singleton_class.prepend(Module.new do
  define_method(:setup_middleware) do |builder|
    if name == "RealWorld::App" && Thread.current != Thread.main
      entered << true
      release.pop
    end
    super(builder)
    if name == "RealWorld::App"
      builds << Datadog::Tracing::Contrib::Sinatra::Framework.middlewares(builder)
        .include?(Datadog::Tracing::Contrib::Rack::TraceMiddleware)
    end
  end
end)

startup = ARGV.shift
identity, arguments = $0.dup, ARGV.dup
load startup
raise "entrypoint changed" unless $0 == identity && ARGV == arguments
threads = 2.times.map { Thread.new { RealWorld::App.prototype } }
prototypes = Timeout.timeout(5) do
  sleep 0.001 while threads.any?(&:alive?) && entered.size < 2
  2.times { release << true }
  threads.map(&:value)
end
raise "concurrent middleware builds" unless builds.size == 1
raise "Rack tracing missing" unless builds.pop
raise "prototype was rebuilt" unless prototypes.map(&:object_id).uniq.size == 1
puts "Sinatra startup preserves Rack tracing and concurrent prototype reuse"
