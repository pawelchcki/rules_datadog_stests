# frozen_string_literal: true

# Versioned Sinatra fixtures activate their locked gems without Bundler.
# The matrix payload uses Datadog's Ruby transport; optional profiling,
# crashtracking and AppSec extensions are not built or claimed here.
require File.join(File.dirname(File.realpath(__FILE__)), "paths.rb")
require "rubygems"
require "rbconfig"
require "json"

module RulesStestsDatadog
  def self.activate!
    return if @activated
    raise LoadError, "recursive Datadog activation" if @activating
    @activating = true
    root = File.dirname(File.realpath(__FILE__))
    abi = JSON.parse(File.read(File.join(root, "abi.json")))
    actual = {"ruby_version" => RbConfig::CONFIG.fetch("ruby_version"), "arch" => RbConfig::CONFIG.fetch("arch")}
    raise LoadError, "Datadog Ruby ABI mismatch: expected #{abi}, got #{actual}" unless abi == actual
    specs = Dir[File.join(root, "specifications", "*.gemspec")].sort.each_with_object({}) do |path, result|
      spec = Gem::Specification.load(File.realpath(path))
      raise LoadError, "invalid Datadog gem specification: #{path}" unless spec
      result[spec.name] = spec
    end
    tracer = specs.fetch("datadog") { raise LoadError, "missing locked Datadog gem" }
    raise LoadError, "expected Datadog 2.43.0, got #{tracer.version}" unless tracer.version.to_s == "2.43.0"
    if (loaded = Gem.loaded_specs["datadog"]) && loaded.version != tracer.version
      raise LoadError, "incompatible already activated Datadog #{loaded.version}"
    end
    selected = {}
    visit = lambda do |spec|
      return if selected.key?(spec.name)
      selected[spec.name] = spec
      spec.runtime_dependencies.each do |dependency|
        candidate = Gem.loaded_specs[dependency.name] || specs[dependency.name]
        unless candidate && dependency.matches_spec?(candidate)
          raise LoadError, "incompatible or missing Datadog dependency #{dependency}; selected #{candidate&.version}"
        end
        visit.call(candidate)
      end
    end
    visit.call(tracer)
    selected.each_value do |spec|
      next if Gem.loaded_specs.key?(spec.name)
      if spec.name == "msgpack" && !File.file?(File.join(root, "gems", spec.full_name, "lib/msgpack/msgpack.so"))
        raise LoadError, "missing native extensions for Datadog dependency #{spec.full_name} (#{actual})"
      end
      spec.full_require_paths.each do |path|
        raise LoadError, "missing Datadog dependency load path: #{path}" unless File.directory?(path)
        $LOAD_PATH << path unless $LOAD_PATH.include?(path)
      end
      # Bundler's require hook consults activated metadata, not GEM_PATH.
      Gem.loaded_specs[spec.name] = spec
    end
    if Gem.loaded_specs.key?("railties")
      # Rails auto-instrumentation runs from a Railtie once the app has loaded.
      require "rails"
      require "active_record"
      require "action_controller/railtie"
    else
      # Elsewhere auto-instrumentation patches only libraries already loaded,
      # once, so load the application's instrumented libraries first.
      %w[io/wait sinatra/base sequel].each { |path| require path }
      require "net/http"
    end
    require "datadog/auto_instrument"
    raise LoadError, "MessagePack native extension did not load" unless $LOADED_FEATURES.any? { |path| path.end_with?("/msgpack/msgpack.so") }
    if ENV["RULES_STESTS_PROBES"] == "true"
      Datadog.configure do |config|
        if (pattern = ENV["DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP"])
          config.tracing.instrument :rack, quantize: {query: {show: :all, obfuscate: {regex: Regexp.new(pattern)}}}
        end
        config.tracing.partial_flush.enabled = ENV["DD_TRACE_PARTIAL_FLUSH_ENABLED"] == "true"
        config.tracing.partial_flush.min_spans_threshold = Integer(ENV.fetch("DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", "500"))
      end
    end
    if ENV["RULES_STESTS_PROBES"] == "true" || ENV["RULES_STESTS_SQL_MARKERS"] == "true"
      require File.join(root, "probes.rb")
    end
    @activated = true
  ensure
    @activating = false
  end
end

RulesStestsDatadog.activate!
